# %% this idea is from https://www.kaggle.com/c/porto-seguro-safe-driver-prediction/discussion/44629
import os
os.environ["CUDA_VISIBLE_DEVICES"] = '2'
base_path = '/home/tom/mywork/some_test/porto_seguro/input/'

model_name = 'porto_seguro_dae006'
data_path = '/home/tom/data/porto_seguro_dae'
model_path = os.path.join(data_path, model_name)
if not os.path.exists(model_path):
    os.makedirs(model_path)

# %% The last cell preprocess data. In this cell, let data go into one model
# in dae003, try hyperopt
import os
import pandas as pd
import numpy as np
import hyperopt as hp
from hyperopt import fmin, rand, tpe, hp, pyll, base, space_eval, Trials
from hyperopt.pyll import Apply, as_apply, dfs, scope
from hyperopt.mongoexp import MongoTrials
from PPMoney.core.utils import proc_run
from PPMoney.core.model.space import space_lgb_binary, space_update, sample_int
from PPMoney.core.data import HDFDataSet
suggest = rand.suggest

data_root = model_path
model_root = os.path.join(data_root, "test_model")#"/tmp/test_model"

from PPMoney.core.data import HDFDataSet
file_tr = os.path.join(data_path, 'mjahrer_1st_train.dataset')
dataset_tr = HDFDataSet(file_tr, chunk_size=2048)
X_0 = dataset_tr['quantile_feature']

file_t = os.path.join(data_path, 'mjahrer_1st_test.dataset')
dataset_t = HDFDataSet(file_t, chunk_size=2048)
X_1 = dataset_t['quantile_feature']

X_all = np.vstack((X_0, X_1))
print(X_all.shape)


# %% build graph
import tensorflow as tf
tf.reset_default_graph()

dae_params = {
    "layers": [1500, 1500, 1500],
    "learning_rate": 5e-4, # 3e-3,
    "minibatch_size": 128,
    "learning_rate_decay": 0.995,
    "keep_rate": 0.7,
    "input_swap_noise": 0.15,
    "noise_std": 0.0,
    "n_epochs": 1000 # 1000
}

def gaussian_noise_layer(input_layer, std, name):
    noise = tf.random_normal(shape=tf.shape(input_layer), mean=0.0, stddev=std, dtype=tf.float32, name=name)
    return input_layer + noise
def standard_layer(input_layer, n_nodes, std, keep_rate, bn_phase, layer_name, vscope_name=None):
    # with tf.variable_scope(vscope_name): # you can use scope on outside wrapper
    layer_o = tf.layers.dense(input_layer, n_nodes, name='{}_dense'.format(layer_name))
    layer_o = tf.layers.batch_normalization(layer_o, name='{}_bn'.format(layer_name), training=bn_phase)
    layer_o = tf.nn.relu(layer_o, name='{}_relu'.format(layer_name))
    layer_o = tf.nn.dropout(layer_o, keep_rate, name='{}_dropout'.format(layer_name))
    layer_o = gaussian_noise_layer(layer_o, std, name='{}_gn'.format(layer_name))
    return layer_o

in_dim = X_0.shape[1]
BATCH_SIZE = dae_params['minibatch_size']

keep_rate = tf.placeholder_with_default(1.0, shape=())
input_swap_noise = tf.placeholder_with_default(0.0, shape=())
noise_std = tf.placeholder_with_default(0.0, shape=())
bn_phase = tf.placeholder_with_default(True, shape=()) # True for train, False for test(emmm...#TODO)
tf_x = tf.placeholder(tf.float32, [None, in_dim]) #  X_train.shape
tf_y = tf.placeholder(tf.int32, None)

with tf.variable_scope('denoise_autoencoder'):
    layer_noise = gaussian_noise_layer(tf_x, input_swap_noise, name='input_gn')
    layer1 = standard_layer(layer_noise, dae_params["layers"][0], noise_std, keep_rate, bn_phase, 'layer1')
    layer2 = standard_layer(layer1, dae_params["layers"][1], noise_std, keep_rate, bn_phase, 'layer2')
    layer3 = standard_layer(layer2, dae_params["layers"][2], noise_std, keep_rate, bn_phase, 'layer3')
    semi_out = standard_layer(layer3, in_dim, noise_std, keep_rate, bn_phase, 'semi_out') # FIXME: just use dense is OK, shouldnot use dropout and relu

loss_semi = tf.losses.mean_squared_error(labels=tf_x, predictions=semi_out)

optimizer = tf.train.RMSPropOptimizer(learning_rate = dae_params['learning_rate'])

update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
with tf.control_dependencies(update_ops):
    train_op_semi = optimizer.minimize(loss_semi)
# train_op_semi = optimizer.minimize(loss_semi)

sess = tf.Session()                                                                 # control training and others
init_op = tf.group(tf.global_variables_initializer(), tf.local_variables_initializer())
sess.run(init_op)     # initialize var in graph

tf.summary.scalar('loss', loss_semi)
tf.summary.histogram('layer1', layer1)
tf.summary.histogram('layer2', layer2)
tf.summary.histogram('layer3', layer3)
writer = tf.summary.FileWriter( os.path.join(model_path, 'log'), sess.graph)     # write to file
merge_op = tf.summary.merge_all()
# %% begin to train
def dae_generator(X, batch_size=64):
    N = X.shape[0]
    m = batch_size
    steps = (N-1)//m + 1
    pad_size = steps*m - N
    # Xpad = np.vstack(X, randomchoose(X, padsize))
    while True:
        for i in range(steps):
            X_batch = X[(m*i):(m*(i+1)), :]
            if pad_size > 0 and i == steps-1:
                rand_idx = np.random.choice(N, pad_size)
                X_batch = np.vstack((X_batch, X[rand_idx]))
            yield X_batch

shuffle_random_range = np.arange(X_all.shape[0])
for i in range(6):
    np.random.shuffle(shuffle_random_range)
X_all = X_all[shuffle_random_range]

all_stage = 0
N_all = X_all.shape[0]
m_bn = dae_params['minibatch_size']
for i1 in range(dae_params['n_epochs']):
    batch_gen = dae_generator(X_all, batch_size=dae_params['minibatch_size'])
    n_steps = (N_all-1) // m_bn + 1
    for step in range(n_steps):
        # train and net output
        X_batch = next(batch_gen)
        result, loss_step, _ = sess.run([merge_op, loss_semi, train_op_semi],
                                {tf_x: X_batch,
                                 keep_rate: dae_params['keep_rate'],
                                 input_swap_noise: dae_params['input_swap_noise'],
                                 noise_std: dae_params['noise_std'],
                                 bn_phase: True})
        all_stage += 1
        if step % 100 == 0:
            writer.add_summary(result, all_stage)
            print(f'Epoch {i1}, Step {step}: mean_squared_error: {loss_step}')

# %% prediction after training

def predict_by_minibatch(X_input, layers_l, batch_size, phase_train=True):
    hidden = np.zeros((X_input.shape[0], sum(layers_l)))
    i = 0
    while True:
        X_input_batch = X_input[i*batch_size:(i+1)*batch_size,:]
        hidden1,hidden2,hidden3 = sess.run([layer1, layer2, layer3], {tf_x: X_input_batch, bn_phase: phase_train})
        hidden[i*batch_size:(i+1)*batch_size,:] += np.hstack((hidden1,hidden2,hidden3))
        i += 1
        if i*batch_size >= X_input.shape[0]:
            break
    return hidden

import gc
X_0_hidden = predict_by_minibatch(X_0, [1500,1500,1500], 10000, phase_train=False)
dataset_tr = HDFDataSet(os.path.join(data_path, 'mjahrer_1st_train.dataset'), chunk_size=2048)
dataset_tr['dae_hidden_feature'] = X_0_hidden
del X_0_hidden
gc.collect()

X_1_hidden = predict_by_minibatch(X_1, [1500,1500,1500], 10000)
dataset_t = HDFDataSet(os.path.join(data_path, 'mjahrer_1st_test.dataset'), chunk_size=2048)
dataset_t['dae_hidden_feature'] = X_1_hidden
del X_1_hidden
gc.collect()

# print(f'X_0_hidden, X_1_hidden: {X_0_hidden.shape, X_1_hidden.shape}')


# %%
