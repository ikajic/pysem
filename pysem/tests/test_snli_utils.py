import numpy as np

from pysem.networks import RecurrentNetwork, DependencyNetwork
from pysem.utils.ml import LogisticRegression
from pysem.utils.snli import CompositeModel, bow_accuracy
from sklearn.feature_extraction.text import CountVectorizer

dim = 25


def test_bow_accuracy(snli):
    snli.reset_streams()
    snli.extractor = snli.get_xy_pairs
    data = [pair for pair in snli.train_data if pair.label != '-']

    vectorizer = CountVectorizer(binary=True)
    vectorizer.fit(snli.vocab)

    scale = 1 / np.sqrt(dim)
    size = (dim, len(vectorizer.get_feature_names()))
    bow_embedding_matrix = np.random.normal(loc=0, scale=scale, size=size)
    bow_classifier = LogisticRegression(n_features=2*dim, n_labels=3)

    acc = bow_accuracy(data, bow_classifier, bow_embedding_matrix, vectorizer)

    assert 0 < acc < 0.65


def test_rnn_accuracy(snli):
    snli.reset_streams()
    snli.extractor = snli.get_xy_pairs

    encoder = RecurrentNetwork(dim=dim, vocab=snli.vocab)
    classifier = LogisticRegression(n_features=2*dim, n_labels=3)
    model = CompositeModel(snli, encoder, classifier)
    acc = model.rnn_accuracy()

    assert 0 < acc < 0.65

    model.train(epochs=0.1, bsize=1, rate=0.01, acc_interval=1)
    assert len(model.acc) > 3


def test_dnn_accuracy(snli):
    snli.reset_streams()
    snli.extractor = snli.get_xy_pairs

    encoder = DependencyNetwork(dim=dim, vocab=snli.vocab)
    classifier = LogisticRegression(n_features=2*dim, n_labels=3)
    model = CompositeModel(snli, encoder, classifier)
    acc = model.dnn_accuracy()

    assert 0 < acc < 0.65

    model.train(epochs=0.1, bsize=1, rate=0.01, acc_interval=1)
    assert len(model.acc) > 3