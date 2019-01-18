###################################### i am doing study this code
###################################### (note) this code is from "https://www.kaggle.com/ahogrammer/attention-based-lstm/notebook"

import os
import re
import numpy as np
import pandas as pd
import tensorflow as tf
import keras.backend as K
from keras.preprocessing import text, sequence
from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences
from keras.engine.topology import Layer
from keras import initializers, regularizers, constraints
from keras.layers import *
from keras.models import Model

pd.set_option('display.max_columns', 10)
pd.set_option('display.width', 500)
pd.set_option('max_colwidth', 150)
pd.set_option('display.max_rows', 200)

# For reproducibility.
seed = 7
np.random.seed(seed)
tf.set_random_seed(seed)
session_conf = tf.ConfigProto(
    intra_op_parallelism_threads=1,
    inter_op_parallelism_threads=1
)
sess = tf.Session(graph=tf.get_default_graph(), config=session_conf)
K.set_session(sess)

#######################################################################################
# Data Loading
#######################################################################################
# we load the dataset and apply some transformations to use it in a deep learning model.
DATA_DIR = 'C:/Users/JongRyul/PycharmProjects/nlp/data/review_data'
train_file = os.path.join(DATA_DIR, 'train.tsv')
test_file = DATA_DIR + "/test.tsv" # os.path.join(DATA_DIR, 'test.tsv')
df_train = pd.read_table(train_file)
df_test = pd.read_table(test_file)

# In addition to the datasets, we load a pretrained word embeddings.
EMBEDDING_FILE = 'C:/Users/JongRyul/PycharmProjects/nlp/data/fasttext_word_vec_pre_trained/crawl-300d-2M.vec' # fasttext-crawl-300d-2m/

def load_embeddings(filename):
    embeddings = {}
    with open(filename, 'rt', encoding='UTF8') as f:
        for line in f:
            values = line.rstrip().split(' ')
            word = values[0]
            vector = np.asarray(values[1:], dtype='float32')
            embeddings[word] = vector
    return embeddings

embeddings = load_embeddings(EMBEDDING_FILE) # {word: vec}

#######################################################################################
# Preprocessings
#######################################################################################
# Negation  handling; [aren't] => [are not]
df_train.Phrase = df_train.Phrase.str.replace("n't", 'not')
df_test.Phrase = df_test.Phrase.str.replace("n't", 'not')

# Replacing numbers; [1924] => [0]
df_train.Phrase = df_train.Phrase.apply(lambda x: re.sub(r'[0-9]+', '0', x))
df_test.Phrase = df_test.Phrase.apply(lambda x: re.sub(r'[0-9]+', '0', x))

x_train = df_train['Phrase'].values
x_test  = df_test['Phrase'].values
y_train = df_train['Sentiment'].values
x = np.r_[x_train, x_test]

# Tokenization; [splitting] text by [space] or [punctuation marks].
tokenizer = Tokenizer(lower=True, filters='\n\t')
tokenizer.fit_on_texts(x)
x_train = tokenizer.texts_to_sequences(x_train)
x_test = tokenizer.texts_to_sequences(x_test)
vocab_size = len(tokenizer.word_index) + 1  # +1 is for zero padding.
print('vocabulary size: {}'.format(vocab_size))

# Zero padding; ensuring that all sentences has the [same length].
maxlen = len(max((s for s in np.r_[x_train, x_test]), key=len))
x_train = sequence.pad_sequences(x_train, maxlen=maxlen, padding='post')
x_test = sequence.pad_sequences(x_test, maxlen=maxlen, padding='post')
print('maxlen: {}'.format(maxlen))
print(x_train.shape)
print(x_test.shape)

def filter_embeddings(embeddings, word_index, vocab_size, dim=300):
    embedding_matrix = np.zeros([vocab_size, dim])
    for word, i in word_index.items():
        if i >= vocab_size:
            continue
        vector = embeddings.get(word)
        if vector is not None:
            embedding_matrix[i] = vector
    return embedding_matrix

embedding_size = 300
embedding_matrix = filter_embeddings(embeddings, tokenizer.word_index,
                                     vocab_size, embedding_size)
print('OOV: {}'.format(len(set(tokenizer.word_index) - set(embeddings))))

#######################################################################################
# Building a model
# In this time, we will use attention based LSTM model. First of all, we should define the attention layer as follows:
#######################################################################################
class Attention(Layer):
    """
    Keras Layer that implements an Attention mechanism for temporal data.
    Supports Masking.
    Follows the work of Raffel et al. [https://arxiv.org/abs/1512.08756]
    # Input shape
        3D tensor with shape: `(samples, steps, features)`.
    # Output shape
        2D tensor with shape: `(samples, features)`.
    :param kwargs:
    Just put it on top of an RNN Layer (GRU/LSTM/SimpleRNN) with return_sequences=True.
    The dimensions are inferred based on the output shape of the RNN.
    Example:
        model.add(LSTM(64, return_sequences=True))
        model.add(Attention())
    """
    def __init__(self, step_dim,
                 W_regularizer=None, b_regularizer=None,
                 W_constraint=None, b_constraint=None,
                 bias=True, **kwargs):
        self.supports_masking = True
        self.init = initializers.get('glorot_uniform')

        self.W_regularizer = regularizers.get(W_regularizer)
        self.b_regularizer = regularizers.get(b_regularizer)

        self.W_constraint = constraints.get(W_constraint)
        self.b_constraint = constraints.get(b_constraint)

        self.bias = bias
        self.step_dim = step_dim
        self.features_dim = 0
        super(Attention, self).__init__(**kwargs)

    def build(self, input_shape):
        assert len(input_shape) == 3

        self.W = self.add_weight((input_shape[-1],),
                                 initializer=self.init,
                                 name='{}_W'.format(self.name),
                                 regularizer=self.W_regularizer,
                                 constraint=self.W_constraint)
        self.features_dim = input_shape[-1]

        if self.bias:
            self.b = self.add_weight((input_shape[1],),
                                     initializer='zero',
                                     name='{}_b'.format(self.name),
                                     regularizer=self.b_regularizer,
                                     constraint=self.b_constraint)
        else:
            self.b = None
        self.built = True

    def compute_mask(self, input, input_mask=None):
        return None

    def call(self, x, mask=None):
        features_dim = self.features_dim
        step_dim = self.step_dim
        eij = K.reshape(K.dot(K.reshape(x, (-1, features_dim)),
                        K.reshape(self.W, (features_dim, 1))), (-1, step_dim))
        if self.bias:
            eij += self.b
        eij = K.tanh(eij)
        a = K.exp(eij)
        if mask is not None:
            a *= K.cast(mask, K.floatx())
        a /= K.cast(K.sum(a, axis=1, keepdims=True) + K.epsilon(), K.floatx())
        a = K.expand_dims(a)
        weighted_input = x * a
        return K.sum(weighted_input, axis=1)

    def compute_output_shape(self, input_shape):
        return input_shape[0],  self.features_dim

# After defining the attention layer, we will define the entire model:
def build_model(maxlen, vocab_size, embedding_size, embedding_matrix):
    input_words = Input((maxlen, ))
    x_words = Embedding(vocab_size,
                        embedding_size,
                        weights=[embedding_matrix],
                        mask_zero=True,
                        trainable=False)(input_words)
    x_words = SpatialDropout1D(0.3)(x_words)
    x_words = Bidirectional(LSTM(50, return_sequences=True))(x_words)
    x = Attention(maxlen)(x_words)
    x = Dropout(0.2)(x)
    x = Dense(50, activation='relu')(x)
    x = Dropout(0.2)(x)
    pred = Dense(5, activation='softmax')(x)

    model = Model(inputs=input_words, outputs=pred)
    return model

model = build_model(maxlen, vocab_size, embedding_size, embedding_matrix)
model.compile(optimizer='nadam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
model.summary()

#######################################################################################
# Training the model
#######################################################################################
save_file = 'model.h5'
history = model.fit(x_train, y_train,
                    epochs=10, verbose=1,
                    batch_size=1024, shuffle=True)

#######################################################################################
# Making a submission file
#######################################################################################
# After training the model, we make a submission file by predicting for the test dataset.
y_pred = model.predict(x_test, batch_size=1024)
y_pred = y_pred.argmax(axis=1).astype(int)
y_pred.shape

mapping = {phrase: sentiment for _, _, phrase, sentiment in df_train.values}

# Overlapping
for i, phrase in enumerate(df_test.Phrase.values):
    if phrase in mapping:
        y_pred[i] = mapping[phrase]

df_test['Sentiment'] = y_pred
df_test[['PhraseId', 'Sentiment']].to_csv('submission.csv', index=False)

#######################################################################################
#
#######################################################################################

