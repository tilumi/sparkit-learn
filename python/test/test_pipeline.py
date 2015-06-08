import shutil
import tempfile

import numpy as np
import scipy.sparse as sp
from common import SplearnTestCase

from sklearn.base import clone
from sklearn.utils.testing import assert_raises, assert_raises_regex
from sklearn.utils.testing import assert_equal
from sklearn.utils.testing import assert_false
from sklearn.utils.testing import assert_true
from sklearn.utils.testing import assert_array_equal
from sklearn.utils.testing import assert_array_almost_equal
from sklearn.datasets import make_classification
#from sklearn.datasets import load_iris

from sklearn.pipeline import Pipeline, FeatureUnion
from splearn.pipeline import SparkPipeline, SparkFeatureUnion, make_sparkunion

from sklearn.feature_extraction.text import CountVectorizer
from splearn.feature_extraction.text import SparkCountVectorizer

from sklearn.linear_model.logistic import LogisticRegression
from splearn.linear_model.logistic import SparkLogisticRegression

from sklearn.feature_selection import VarianceThreshold
from splearn.feature_selection import SparkVarianceThreshold

from splearn.decomposition import SparkTruncatedSVD

from splearn.rdd import ArrayRDD, DictRDD


class PipelineTestCase(SplearnTestCase):

    def setUp(self):
        super(PipelineTestCase, self).setUp()
        self.outputdir = tempfile.mkdtemp()

    def tearDown(self):
        super(PipelineTestCase, self).tearDown()
        shutil.rmtree(self.outputdir)

    def generate_junkfood(self, blocks=None):
        X = (
            "the pizza pizza beer copyright",
            "the pizza burger beer copyright",
            "the the pizza beer beer copyright",
            "the burger beer beer copyright",
            "the coke burger coke copyright",
            "the coke burger burger",
        )
        Z_rdd = self.sc.parallelize(X)
        Z = ArrayRDD(Z_rdd, bsize=blocks)
        return X, Z

    # def generate_iris(self, blocks=None):
    #     iris = load_iris()

    #     X = iris.data
    #     X -= X.mean(axis=0)
    #     y = iris.target

    #     X_rdd = self.sc.parallelize(X)
    #     y_rdd = self.sc.parallelize(y)
    #     Z_rdd = X_rdd.zip(y_rdd)
    #     Z = DictRDD(Z_rdd, columns=('X', 'y'), bsize=blocks)

    #     return X, y, Z

    def generate_dataset(self, n_classes, n_samples, blocks=None):
        X, y = make_classification(n_classes=n_classes,
                                   n_samples=n_samples, n_features=10,
                                   n_informative=4, n_redundant=0,
                                   n_clusters_per_class=1,
                                   random_state=42)

        X_rdd = self.sc.parallelize(X, 4)
        y_rdd = self.sc.parallelize(y, 4)

        Z = DictRDD(X_rdd.zip(y_rdd), columns=('X', 'y'), bsize=blocks)

        return X, y, Z


class TestFeatureUnion(PipelineTestCase):

    def test_same_result(self):
        X, Z = self.generate_junkfood(2)

        loc_char = CountVectorizer(analyzer="char_wb", ngram_range=(3, 3))
        dist_char = SparkCountVectorizer(analyzer="char_wb", ngram_range=(3, 3))

        loc_word = CountVectorizer(analyzer="word")
        dist_word = SparkCountVectorizer(analyzer="word")

        loc_union = FeatureUnion([
            ("chars", loc_char),
            ("words", loc_word)
        ])
        dist_union = SparkFeatureUnion([
            ("chars", dist_char),
            ("words", dist_word)
        ])
        # test same feature names
        loc_union.fit(X)
        dist_union.fit(Z)
        assert_equal(
            loc_union.get_feature_names(),
            dist_union.get_feature_names()
        )
        # test same results
        X_transformed = loc_union.transform(X)
        Z_transformed = sp.vstack(dist_union.transform(Z).collect())
        assert_array_equal(X_transformed.toarray(), Z_transformed.toarray())
        # test same results with fit_transform
        X_transformed = loc_union.fit_transform(X)
        Z_transformed = sp.vstack(dist_union.fit_transform(Z).collect())
        assert_array_equal(X_transformed.toarray(), Z_transformed.toarray())
        # test same results in parallel
        loc_union_par = FeatureUnion([
            ("chars", loc_char),
            ("words", loc_word)
        ], n_jobs=2)
        dist_union_par = SparkFeatureUnion([
            ("chars", dist_char),
            ("words", dist_word)
        ], n_jobs=2)

        loc_union_par.fit(X)
        dist_union_par.fit(Z)
        X_transformed = loc_union_par.transform(X)
        Z_transformed = sp.vstack(dist_union_par.transform(Z).collect())
        assert_array_equal(X_transformed.toarray(), Z_transformed.toarray())

    def test_same_result_weight(self):
        X, Z = self.generate_junkfood(2)

        loc_char = CountVectorizer(analyzer="char_wb", ngram_range=(3, 3))
        dist_char = SparkCountVectorizer(analyzer="char_wb", ngram_range=(3, 3))

        loc_word = CountVectorizer(analyzer="word")
        dist_word = SparkCountVectorizer(analyzer="word")

        loc_union = FeatureUnion([
            ("chars", loc_char),
            ("words", loc_word)
        ], transformer_weights={"words": 10})
        dist_union = SparkFeatureUnion([
            ("chars", dist_char),
            ("words", dist_word)
        ], transformer_weights={"words": 10})

        loc_union.fit(X)
        dist_union.fit(Z)

        X_transformed = loc_union.transform(X)
        Z_transformed = sp.vstack(dist_union.transform(Z).collect())
        assert_array_equal(X_transformed.toarray(), Z_transformed.toarray())

    def test_make_union(self):
        svd = SparkTruncatedSVD()
        mock = TransfT()
        fu = make_sparkunion(svd, mock)
        names, transformers = zip(*fu.transformer_list)
        assert_equal(names, ("sparktruncatedsvd", "transft"))
        assert_equal(transformers, (svd, mock))



# ------------------------- Pipeline tests -------------------
class IncorrectT(object):
    """Small class to test parameter dispatching.
    """

    def __init__(self, a=None, b=None):
        self.a = a
        self.b = b


class T(IncorrectT):

    def fit(self, Z):
        return self

    def get_params(self, deep=False):
        return {'a': self.a, 'b': self.b}

    def set_params(self, **params):
        self.a = params['a']
        return self


class TransfT(T):

    def transform(self, Z):
        return Z


class FitParamT(object):
    """Mock classifier
    """

    def __init__(self):
        self.successful = False
        pass

    def fit(self, Z, should_succeed=False):
        self.successful = should_succeed

    def predict(self, Z):
        return self.successful


class TestPipeline(PipelineTestCase):

    def test_pipeline_init(self):
        # Test the various init parameters of the pipeline.
        assert_raises(TypeError, SparkPipeline)
        # Check that we can't instantiate pipelines with objects without fit
        # method
        pipe = assert_raises(TypeError, SparkPipeline, [('svc', IncorrectT)])
        # Smoke test with only an estimator
        clf = T()
        pipe = SparkPipeline([('svc', clf)])
        assert_equal(pipe.get_params(deep=True),
                     dict(svc__a=None, svc__b=None, svc=clf,
                         **pipe.get_params(deep=False)
                         ))

        # Check that params are set
        pipe.set_params(svc__a=0.1)
        assert_equal(clf.a, 0.1)
        assert_equal(clf.b, None)
        # Smoke test the repr:
        repr(pipe)

        # Test with two objects
        vect = SparkCountVectorizer()
        filter = SparkVarianceThreshold()
        pipe = SparkPipeline([('vect', vect), ('filter', filter)])

        # Check that we can't use the same stage name twice
        assert_raises(ValueError, SparkPipeline, [('vect', vect), ('vect', vect)])

        # Check that params are set
        pipe.set_params(vect__min_df=0.1)
        assert_equal(vect.min_df, 0.1)
        # Smoke test the repr:
        repr(pipe)

        # Check that params are not set when naming them wrong
        assert_raises(ValueError, pipe.set_params, filter__min_df=0.1)

        # Test clone
        pipe2 = clone(pipe)
        assert_false(pipe.named_steps['vect'] is pipe2.named_steps['vect'])

        # Check that apart from estimators, the parameters are the same
        params = pipe.get_params(deep=True)
        params2 = pipe2.get_params(deep=True)

        for x in pipe.get_params(deep=False):
            params.pop(x)

        for x in pipe2.get_params(deep=False):
            params2.pop(x)

        # Remove estimators that where copied
        params.pop('vect')
        params.pop('filter')
        params2.pop('vect')
        params2.pop('filter')
        assert_equal(params, params2)

    def test_pipeline_same_results(self):
        X, y, Z = self.generate_dataset(2, 10000, 2000)

        loc_clf = LogisticRegression()
        loc_filter = VarianceThreshold()
        loc_pipe = Pipeline([
            ('threshold', loc_filter),
            ('logistic', loc_clf)
        ])

        dist_clf = SparkLogisticRegression()
        dist_filter = SparkVarianceThreshold()
        dist_pipe = SparkPipeline([
            ('threshold', dist_filter),
            ('logistic', dist_clf)
        ])

        dist_filter.fit(Z)
        loc_pipe.fit(X, y)
        dist_pipe.fit(Z, logistic__classes=np.unique(y))

        assert_true(np.mean(np.abs(
            loc_pipe.predict(X) - \
            np.concatenate(dist_pipe.predict(Z[:, 'X']).collect())
        )) < 0.1)