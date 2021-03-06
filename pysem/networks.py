import pickle
import spacy

import numpy as np

from collections import defaultdict
from pysem.utils.spacy import TokenWrapper
from pysem.utils.vsa import normalize, unitary_vector, get_convolution_matrix
from pysem.utils.multiprocessing import flatten


class SquareZeros(object):
    '''Returns a square array of zeros when called. Used to initialize
    defaultdicts that default to a numpy array of zeros.'''
    def __init__(self, dim):
        self.dim = dim

    def __call__(self):
        return np.zeros((self.dim, self.dim))


class FlatZeros(object):
    '''Returns a flat array of zeros when called. Used to initialize
    defaultdicts that default to a numpy array of zeros.'''
    def __init__(self, dim):
        self.dim = dim

    def __call__(self):
        return np.zeros((self.dim, 1))


class RecursiveModel(object):
    """A base class for networks that use recursive applications of one or
    more set of weights to model sequential data. Recurrent networks model
    sequences by recursively applying weights in a linear chain, while
    dependency networks model sequences by recursively applying weights
    using tree structures.
    """
    parser = spacy.load('en')

    deps = ['compound', 'punct', 'nsubj', 'ROOT', 'det', 'attr', 'cc',
            'npadvmod', 'appos', 'prep', 'pobj', 'amod', 'advmod', 'acl',
            'nsubjpass', 'auxpass', 'agent', 'advcl', 'aux', 'xcomp', 'nmod',
            'dobj', 'relcl', 'nummod', 'mark', 'pcomp', 'conj', 'poss',
            'ccomp', 'oprd', 'acomp', 'neg', 'parataxis', 'dep', 'expl',
            'preconj', 'case', 'dative', 'prt', 'quantmod', 'meta', 'intj',
            'csubj', 'predet', 'csubjpass', '']

    @staticmethod
    def sigmoid(x):
        '''Apply the sigmoid nonlinearity to the input vector.'''
        return 1.0 / (1 + np.exp(-x))

    @staticmethod
    def sigmoid_grad(x):
        '''Compute sigmoid gradient with respect to an input vector.'''
        return x * (1 - x)

    @staticmethod
    def softmax(x):
        '''Compute a softmax on the input vector.'''
        return np.exp(x) / np.sum(np.exp(x), axis=0)

    @staticmethod
    def softplus(x):
        '''Apply the softplus nonlinearity to an input vector.'''
        return np.log(1 + np.exp(x))

    @staticmethod
    def softplus_grad(x):
        '''Compute softplus gradient with respect to an input vector.'''
        return 1.0 / (1.0 + np.exp(-x))

    @staticmethod
    def tanh(x):
        '''Apply the tanh nonlinearity to an input vector.'''
        return np.tanh(x)

    @staticmethod
    def tanh_grad(x):
        '''Compute tanh gradient with respect to an input vector.'''
        return 1.0 - x * x

    @staticmethod
    def gaussian_id(dim):
        '''Returns an identity matrix with gaussian noise added.'''
        identity = np.eye(dim)
        gaussian = np.random.normal(loc=0, scale=0.01, size=(dim, dim))
        return identity + gaussian

    @staticmethod
    def random_weights(d1, d2):
        '''Returns matrix of values sampled uniformly using Glorot init.'''
        eps = np.sqrt(6.0 / (d2 + d1))
        weights = np.random.uniform(-eps, eps, size=(d1, d2))
        return weights

    @staticmethod
    def random_vector(dim):
        '''Returns a random vector from the unit sphere.'''
        scale = 1 / np.sqrt(dim)
        vector = np.random.normal(loc=0, scale=scale, size=(dim, 1))
        return vector

    def pretrained_vecs(self, path):
        '''Load pretrained word embeddings for initialization.'''
        self.vectors = {}
        with open(path, 'rb') as pfile:
            pretrained = pickle.load(pfile)

        for word in self.vocab:
            try:
                self.vectors[word] = pretrained[word].reshape(self.dim, 1)
            except KeyError:
                scale = 1 / np.sqrt(self.dim)
                randvec = np.random.normal(0, scale=scale, size=(self.dim, 1))
                self.vectors[word] = normalize(randvec)

    def random_vecs(self):
        '''Use random word embeddings for initialization.'''
        scale = 1 / np.sqrt(self.dim)
        self.vectors = {word: normalize(np.random.normal(loc=0, scale=scale,
                        size=(self.dim, 1))) for word in self.vocab}


class RecurrentNetwork(RecursiveModel):
    """A plain recurrent network that computes a hidden state given an input
    and the previous hidden state. The computed hidden state therefore depends
    on both the current input and the entire history of the input sequence up
    to this point. This implementation is designed to compress a sequence into
    a single hidden representation rather than make a prediction for each item
    in the input sequence. Batched computation is assumed by default, such
    that each forward and backward pass will involve multiple input sentences.

    Parameters:
    ----------
    dim : int
        The dimensionality of the hidden state representations.
    vocab : list of strings
        The vocabulary of possible input items.

    Attributes:
    -----------
    dim : int
        The dimensionality of the hidden state representations.
    vocab : list of strings
        The vocabulary of possible input items.
    whh : numpy.ndarray
        The hidden-to-hidden weight matrix.
    why : numpy.ndarray
        The hidden-to-output weight matrix.
    bh : numpy.ndarray
        The bias vector on the hidden state.
    by : numpy.ndarray
        The bias vector on the output state.
    vectors : dict
        Matches each vocabulary item with a vector embedding that is learned
        over the course of training the network.
    """
    def __init__(self, dim, vocab, pretrained=False):
        self.dim = dim
        self.vocab = vocab
        self.clipflag = None

        self.xs, self.hs = {}, {}
        self.params, self.gradients = {}, {}

        for w in ['Whh', 'Why']:
            self.params[w] = self.gaussian_id(dim)

        for b in ['bh', 'by']:
            self.params[b] = np.zeros((dim, 1))

        self.pretrained_vecs(pretrained) if pretrained else self.random_vecs()

    def clip_gradient(self, gradient, clipval=5):
        '''Clip a large gradient so that its norm is equal to clipval.'''
        norm = np.linalg.norm(gradient)
        if norm > clipval:
            self.clipflag = True
            gradient = (gradient / norm) * 5

        return gradient

    def to_array(self, words):
        '''Compute input array from words in a given sequence position.'''
        array = np.zeros((self.dim, self.bsize))
        for idx, word in enumerate(words):
            if word != 'PAD':
                try:
                    array[:, idx] = self.vectors[word].flatten()
                except KeyError:
                    pass
        return array

    def compute_embeddings(self):
        '''Compute network hidden states for each item in the sequence.'''
        self.hs[-1] = np.zeros((self.dim, self.bsize))

        for i in range(self.seqlen):
            words = [sequence[i] for sequence in self.batch]
            self.xs[i] = words
            self.hs[i] = np.dot(self.params['Whh'], self.hs[i-1])
            self.hs[i] += self.to_array(words)
            self.hs[i] = np.tanh(self.hs[i] + self.params['bh'])

        self.ys = np.dot(self.params['Why'], self.hs[i]) + self.params['by']
        self.ys = np.tanh(self.ys)

    def forward_pass(self, batch):
        '''Convert input sentences into sequence and compute hidden states.'''
        self.batch = [[n.text for n in self.parser(sen)] for sen in batch]
        self.bsize = len(batch)
        self.seqlen = max([len(s) for s in self.batch])

        for x in range(self.bsize):
            diff = self.seqlen - len(self.batch[x])
            self.batch[x] = ['PAD' for _ in range(diff)] + self.batch[x]

        self.compute_embeddings()

    def backward_pass(self, error_grad, rate=0.1):
        '''Compute gradients for hidden-to-hidden weight matrix and input word
        vectors before performing weight updates.'''
        error_grad = error_grad * self.tanh_grad(self.get_root_embedding())
        self.xgrads = defaultdict(FlatZeros(self.dim))

        dwhh = np.zeros_like(self.params['Whh'])
        dbh = np.zeros_like(self.params['bh'])

        dwhy = np.dot(error_grad, self.hs[self.seqlen-1].T)
        dby = np.sum(error_grad, axis=1).reshape(self.dim, 1)

        dh_next = np.zeros_like(self.hs[0])
        dh = np.dot(self.params['Why'].T, error_grad)
        dh = dh * self.tanh_grad(self.hs[self.seqlen-1])

        for i in reversed(range(self.seqlen)):
            if i < self.seqlen - 1:
                dh = np.dot(self.params['Whh'].T, dh_next)
                dh = dh * self.tanh_grad(self.hs[i])

            dwhh += np.dot(dh_next, self.hs[i].T)
            dbh += np.sum(dh, axis=1).reshape(self.dim, 1)
            dh_next = dh

            for idx, word in enumerate(self.xs[i]):
                if word != 'PAD':
                    try:
                        item = word
                        self.xgrads[item] += dh[:, idx].reshape(self.dim, 1)
                    except KeyError:
                        pass

        self.dwhy = self.clip_gradient(dwhy / self.bsize)
        self.dwhh = self.clip_gradient(dwhh / self.bsize)
        self.dbh = self.clip_gradient(dbh / self.bsize)
        self.dby = self.clip_gradient(dby / self.bsize)
        self.xgrads = {w: g / self.bsize for w, g in self.xgrads.items()}

        self.params['Why'] -= rate * self.dwhy
        self.params['Whh'] -= rate * self.dwhh
        self.params['bh'] -= rate * self.dbh
        self.params['by'] -= rate * self.dby

        word_set = [w for w in set(flatten(self.batch)) if w != 'PAD']
        all_words = [w for w in flatten(self.batch) if w != 'PAD']

        for word in word_set:
            try:
                count = all_words.count(word)
                self.vectors[word] -= rate * self.xgrads[word] / count
            except KeyError:
                pass

    def get_root_embedding(self):
        '''Returns the embeddings for the final/root node in the sequence.'''
        return self.ys


class DependencyNetwork(RecursiveModel):
    """A tree structured recursive network that computes a hidden state for
    each node in the dependency tree for a given input sentence. The hidden
    state at each node is determined by the word associated with the node
    along with all of its dependents. A single distributed representation is
    accordingly produced for the root node of the tree, which provides a
    single representation of the full sentence. The tanh nonlinearity is used
    for computing hidden state representations.

    Parameters:
    ----------
    dim : int
        The dimensionality of the hidden state representation.
    vocab : list of strings
        The vocabulary of possible input items.

    Attributes:
    -----------
    dim : int
        The dimensionality of the hidden state representation.
    vocab : list of strings
        The vocabulary of possible input items.
    parser : callable
        The parser used to produce a dependency tree from an input sentence.
    biases: defaultdict
        Matches each known dependency with a bias vector for the hidden state
        produced for head words whose children occupy this dependency.
    weights : defaultdict
        Matches each known dependency with the corresponding weight matrix.
    wgrads : defaultdict
        Matches each known dependency with the corresponding weight gradient.
    vectors : dict
        Matches each vocabulary item with a vector embedding that is learned
        over the course of training the network.
    tree : list
        A list of the nodes that make the dependency tree for an input
        sentence. Only computed when forward_pass is called on a sentence.
    """
    def __init__(self, dim, vocab, eps=0.3, pretrained=False):
        self.dim = dim
        self.vocab = sorted(list(vocab))
        self.biases = defaultdict(FlatZeros(self.dim))
        self.weights = defaultdict(SquareZeros(self.dim))
        self.pretrained_vecs(pretrained) if pretrained else self.random_vecs()

        for dep in self.deps:
            self.weights[dep] = self.gaussian_id(self.dim)
            self.biases[dep] = np.zeros((self.dim, 1))

        self.wm = self.gaussian_id(self.dim)

    def save(self, filename):
        '''Save model parameters to a pickle file.'''
        params = {'biases': self.biases, 'weights': self.weights,
                  'wm': self.wm, 'vectors': self.vectors, 'vocab': self.vocab,
                  'dim': self.dim}

        with open(filename, 'wb') as pfile:
            pickle.dump(params, pfile)

    def load(self, filename):
        '''Load model parameters from a pickle file.'''
        with open(filename, 'rb') as pfile:
            params = pickle.load(pfile)

        self.dim = params['dim']
        self.vocab = params['vocab']
        self.vectors = params['vectors']
        self.weights = params['weights']
        self.biases = params['biases']
        self.wm = params['wm']

    def reset_comp_graph(self):
        '''Flag all nodes in the graph as being uncomputed.'''
        for node in self.tree:
            node.computed = False

    def clip_gradient(self, node, clipval=5):
        '''Clip a large gradient so that its norm is equal to clipval.'''
        norm = np.linalg.norm(node.gradient)
        if norm > clipval:
            node.gradient = (node.gradient / norm) * clipval

    def compute_gradients(self):
        '''Compute gradients for every weight matrix and embedding by
        recursively computing gradients for embeddings and weight matrices
        whose parents have been computed. Recursion terminates when every
        embedding and weight matrix has a gradient.'''
        for node in self.tree:
            parent = self.get_parent(node)

            if not node.computed and parent.computed:
                self.clip_gradient(parent)
                wgrad = np.outer(parent.gradient, node.embedding)
                cgrad = np.dot(self.weights[node.dep_].T, parent.gradient)

                nlgrad = self.tanh_grad(node.embedding)
                nlgrad = nlgrad.reshape((len(nlgrad), 1))

                self.wgrads[node.dep_] += wgrad
                self.bgrads[node.dep_] += parent.gradient
                node.gradient = cgrad * nlgrad
                node.computed = True
                self._update_word_grad(node)

        if all([node.computed for node in self.tree]):
            return
        else:
            self.compute_gradients()

    def compute_embeddings(self):
        '''Computes embeddings for all nodes in the graph by recursively
        computing the embeddings for nodes whose children have all been
        computed. Recursion terminates when every node has an embedding.'''
        for node in self.tree:
            if not node.computed:
                children = self.get_children(node)
                children_computed = [c.computed for c in children]

                if all(children_computed):
                    self.embed_node(node, children)

        nodes_computed = [node.computed for node in self.tree]
        if all(nodes_computed):
            return
        else:
            self.compute_embeddings()

    def embed_node(self, node, children):
        '''Computes the vector embedding for a node from the vector embeddings
        of its children. In the case of leaf nodes with no children, the
        vector for the word corresponding to the leaf node is used as the
        embedding.'''
        try:
            emb = np.dot(self.wm, np.copy(self.vectors[node.text]))
        except KeyError:
            emb = np.zeros(self.dim).reshape((self.dim, 1))

        for child in children:
            emb += np.dot(self.weights[child.dep_], child.embedding)
            emb += self.biases[child.dep_]

        node.embedding = self.tanh(emb)
        node.computed = True

    def update_word_embeddings(self):
        '''Use node gradients to update the word embeddings at each node.'''
        for node in self.tree:
            try:
                word = node.text
                count = sum([1 for x in self.tree if x.text == word])
                self.vectors[word] -= self.rate * self.xgrads[word] / count
            except KeyError:
                pass

    def update_weights(self):
        '''Use gradients to update the weights/biases for each dependency.'''
        for dep in self.wgrads:
            depcount = len([True for node in self.tree if node.dep_ == dep])
            self.weights[dep] -= self.rate * self.wgrads[dep] / depcount
            self.biases[dep] -= self.rate * self.bgrads[dep] / depcount

        self.wm -= self.rate * (self.dwm / len(self.tree))

    def forward_pass(self, sentence):
        '''Compute activations for every node in the computational graph
        generated from a dependency parse of the provided sentence.'''
        self.tree = [TokenWrapper(token) for token in self.parser(sentence)]
        self.compute_embeddings()
        self.reset_comp_graph()

    def backward_pass(self, error_grad, rate=0.35):
        '''Compute gradients for every weight matrix and input word vector
        used when computing activations in accordance with the comp graph.'''
        self.wgrads = defaultdict(SquareZeros(self.dim))
        self.bgrads = defaultdict(FlatZeros(self.dim))
        self.xgrads = defaultdict(FlatZeros(self.dim))
        self.dwm = np.zeros_like(self.wm)
        self._set_root_gradient(error_grad)
        self.rate = rate

        self.compute_gradients()
        self.update_weights()
        self.update_word_embeddings()

    def get_children(self, node):
        '''Returns all nodes that are children of the provided node.'''
        children = []
        for other_node in self.tree:
            if other_node.idx in [child.idx for child in node.children]:
                children.append(other_node)

        return children

    def get_parent(self, node):
        '''Get the node that is the parent of the supplied node'''
        for other_node in self.tree:
            if other_node.idx == node.head.idx:
                return other_node

    def has_children(self, node):
        '''Check if node has children, return False for leaf nodes.'''
        return bool(node.children)

    def get_root_embedding(self):
        '''Returns the embedding for the root node in the tree.'''
        for node in self.tree:
            if node.head.idx == node.idx:
                return node.embedding

    def _update_word_grad(self, node):
        self.xgrads[node.text] += np.dot(self.wm.T, node.gradient)
        try:
            self.dwm += np.dot(node.gradient, self.vectors[node.text].T)
        except KeyError:
            pass

    def _set_root_gradient(self, grad):
        '''Set the error gradient on the root node in the comp graph.'''
        for node in self.tree:
            if node.head.idx == node.idx:
                embedding = self.get_root_embedding()
                node.gradient = grad * self.tanh_grad(embedding)
                node.computed = True
                self._update_word_grad(node)


class HolographicNetwork(DependencyNetwork):
    """A dependency network with the weights associated with each known
    dependency fixed to compute the circular convolution of the dependent
    embedding with a unitary vector. No nonlinearities are applied, and the
    word embeddings are initialized as random holographic reduced
    representations (HRRs). The only parameters of the model that are learned
    are accordingly the HRRs and the biases on the hidden states. The network
    accordingly learns a set of HRRs that are composed using the usual HRR
    operations of vector addition and circular convolution to yield structured
    representations of sentences that are useful for performing natural
    language inference tasks.

    Parameters:
    ----------
    dim : int
        The dimensionality of the hidden state representations.
    vocab : list of strings
        The vocabulary of possible input items.

    Attributes:
    -----------
    dim : int
        The dimensionality of the hidden state representations.
    vocab : list of strings
        The vocabulary of possible input items.
    parser : callable
        The parser used to produce a dependency tree from an input sentence.
    biases: defaultdict
        Matches each known dependency with a bias vector for the hidden state
        produced for head words whose children occupy this dependency.
    weights : defaultdict
        Matches each known dependency with a fixed unitary matrix.
    vectors : dict
        Matches each vocabulary item with an HRR that is learned over the
        course of training the network.
    tree : list
        A list of the nodes that make the dependency tree for an input
        sentence. Only computed when forward_pass is called on a sentence.
    """
    def __init__(self, dim, vocab):
        self.dim = dim
        self.vocab = sorted(list(vocab))
        self.weights = defaultdict(SquareZeros(dim))
        self.biases = defaultdict(FlatZeros(dim))
        self.random_vecs()

        for dep in self.deps:
            self.weights[dep] = get_convolution_matrix(unitary_vector(dim))

    def embed_node(self, node, children):
        '''Computes the vector embedding for a node from the vector embeddings
        of its children. In the case of leaf nodes with no children, the
        vector for the word corresponding to the leaf node is used as the
        embedding.'''
        try:
            emb = np.copy(self.vectors[node.text])
        except KeyError:
            emb = np.zeros(self.dim).reshape((self.dim, 1))

        for child in children:
            bias = self.biases[child.dep_]
            emb += np.dot(self.weights[child.dep_], child.embedding) + bias

        node.embedding = emb
        node.computed = True

    def backward_pass(self, error_grad, rate=0.35):
        '''Compute gradients for every weight matrix and input word vector
        used when computing activations in accordance with the comp graph.'''
        self.bgrads = defaultdict(FlatZeros(self.dim))
        self.xgrads = defaultdict(FlatZeros(self.dim))
        self._set_root_gradient(error_grad)
        self.rate = rate

        self.compute_gradients()
        self.update_weights()
        self.update_word_embeddings()

    def compute_gradients(self):
        '''Compute gradients for every weight matrix and embedding by
        recursively computing gradients for embeddings and weight matrices
        whose parents have been computed. Recursion terminates when every
        embedding and weight matrix has a gradient.'''
        for node in self.tree:
            parent = self.get_parent(node)

            if not node.computed and parent.computed:
                cgrad = np.dot(self.weights[node.dep_].T, parent.gradient)
                self.bgrads[node.dep_] += parent.gradient
                node.gradient = cgrad
                node.computed = True
                self._update_word_grad(node)

        if all([node.computed for node in self.tree]):
            return
        else:
            self.compute_gradients()

    def update_weights(self):
        '''Use weight gradients to update the weights for each dependency.'''
        for dep in self.bgrads:
            depcount = len([True for node in self.tree if node.dep_ == dep])
            self.biases[dep] -= self.rate * self.bgrads[dep] / depcount

    def _update_word_grad(self, node):
        try:
            self.xgrads[node.text] += node.gradient
        except KeyError:
            pass

    def _set_root_gradient(self, grad):
        '''Set the error gradient on the root node in the comp graph.'''
        for node in self.tree:
            if node.head.idx == node.idx:
                node.gradient = grad
                node.computed = True
                self._update_word_grad(node)
