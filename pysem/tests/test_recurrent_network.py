import os
import random

import numpy as np

from pysem.corpora import SNLI
from pysem.networks import RecurrentNetwork
from pysem.utils.ml import LogisticRegression

snli_path = os.getcwd() + '/pysem/tests/corpora/snli/'


def get_cost(model, logreg, xs, ys):
    model.forward_pass(xs)
    embedding = model.get_root_embedding()

    return logreg.get_cost(embedding, ys)


def num_grad(model, params, idx, xs, ys, logreg, delta=1e-5):
    val = np.copy(params[idx])

    params[idx] = val + delta
    pcost = get_cost(model, logreg, xs, ys)

    params[idx] = val - delta
    ncost = get_cost(model, logreg, xs, ys)

    params[idx] = val
    numerical_gradient = (pcost - ncost) / (2 * delta)

    return numerical_gradient


def test_forward_pass():
    snli = SNLI(snli_path)
    snli.build_vocab()
    snli.extractor = snli.get_sentences

    dim = 50

    rnn = RecurrentNetwork(dim=dim, vocab=snli.vocab)
    sample = next(snli.train_data)
    sen = random.choice(sample)

    rnn.forward_pass(sen)
    sen_vec = rnn.get_root_embedding()

    assert isinstance(sen_vec, np.ndarray)


def test_backward_pass():
    snli = SNLI(snli_path)
    snli.build_vocab()
    snli.extractor = snli.get_sentences

    dim = 50
    eps = 0.5

    rnn = RecurrentNetwork(dim=dim, vocab=snli.vocab)
    sample = next(snli.train_data)
    sen = random.choice(sample)

    error_grad = np.random.random((dim, 1)) * 2 * eps - eps

    rnn.forward_pass(sen)

    # Save a copy of the weights before SGD update
    weights = np.copy(rnn.weights)

    # Do backprop
    rnn.backward_pass(error_grad, rate=0.1)

    new_weights = np.copy(rnn.weights)

    # Check that every weight has changed after the SGD update
    assert np.count_nonzero(weights - new_weights) == weights.size


def test_weight_gradients():
    snli = SNLI(snli_path)
    snli.build_vocab()
    snli.extractor = snli.get_sentences

    dim = 50
    n_labels = 3
    n_gradient_checks = 25

    rnn = RecurrentNetwork(dim=dim, vocab=snli.vocab)
    logreg = LogisticRegression(n_features=dim, n_labels=n_labels)

    sample = next(snli.train_data)
    xs = random.choice(sample)
    ys = np.zeros(n_labels)
    ys[np.random.randint(0, n_labels, 1)] = 1
    ys = ys.reshape(n_labels, 1)

    rnn.forward_pass(xs)

    # Use random weight in each matrix for n numerical gradient checks
    for _ in range(n_gradient_checks):
        idx = np.random.randint(0, rnn.weights.size, size=1)
        params = rnn.weights.flat

        numerical = num_grad(rnn, params, idx, xs, ys, logreg)

        rnn.forward_pass(xs)

        logreg.train(rnn.get_root_embedding(), ys, rate=0.001)

        rnn.backward_pass(logreg.yi_grad, rate=0.001)
        analytic = rnn.dw.flat[idx]

        assert np.allclose(analytic, numerical)


def test_embedding_gradients():
    snli = SNLI(snli_path)
    snli.build_vocab()
    snli.extractor = snli.get_sentences

    dim = 50
    n_labels = 3
    n_gradient_checks = 25

    rnn = RecurrentNetwork(dim=dim, vocab=snli.vocab)
    logreg = LogisticRegression(n_features=dim, n_labels=n_labels)

    sample = next(snli.train_data)
    xs = random.choice(sample)
    ys = np.zeros(n_labels)
    ys[np.random.randint(0, n_labels, 1)] = 1
    ys = ys.reshape(n_labels, 1)

    rnn.forward_pass(xs)

    # Use random weight in each matrix for n numerical gradient checks
    for _ in range(n_gradient_checks):
        idx = np.random.randint(0, rnn.bias.size, size=1)
        params = rnn.bias.flat

        numerical = num_grad(rnn, params, idx, xs, ys, logreg)

        rnn.forward_pass(xs)

        logreg.train(rnn.get_root_embedding(), ys, rate=0.001)

        rnn.backward_pass(logreg.yi_grad, rate=0.001)
        analytic = rnn.db.flat[idx]

        assert np.allclose(analytic, numerical)