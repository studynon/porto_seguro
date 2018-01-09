base_path = '/home/tom/mywork/some_test/porto_seguro/input/'
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.model_selection import StratifiedKFold
from multiprocessing import *
import gc
import warnings
warnings.filterwarnings("ignore")

import xgboost as xgb
#######################################

# Thanks Pascal and the1owl

# Pascal's Recovery https://www.kaggle.com/pnagel/reconstruction-of-ps-reg-03
# Froza's Baseline https://www.kaggle.com/the1owl/forza-baseline

# single XGB LB 0.285 will release soon.

#######################################

#### Load Data
train = pd.read_csv(base_path + 'train.csv')
test = pd.read_csv(base_path + 'test.csv')

###
y = train['target'].values
testid= test['id'].values

train.drop(['id','target'],axis=1,inplace=True)
test.drop(['id'],axis=1,inplace=True)

### Drop calc
unwanted = train.columns[train.columns.str.startswith('ps_calc_')]
train = train.drop(unwanted, axis=1)
test = test.drop(unwanted, axis=1)

### Great Recovery from Pascal's materpiece
### Great Recovery from Pascal's materpiece
### Great Recovery from Pascal's materpiece
### Great Recovery from Pascal's materpiece
### Great Recovery from Pascal's materpiece

def recon(reg):
    integer = int(np.round((40*reg)**2))
    for a in range(32):
        if (integer - a) % 31 == 0:
            A = a
    M = (integer - A)//31
    return A, M
train['ps_reg_A'] = train['ps_reg_03'].apply(lambda x: recon(x)[0])
train['ps_reg_M'] = train['ps_reg_03'].apply(lambda x: recon(x)[1])
train['ps_reg_A'].replace(19,-1, inplace=True)
train['ps_reg_M'].replace(51,-1, inplace=True)
test['ps_reg_A'] = test['ps_reg_03'].apply(lambda x: recon(x)[0])
test['ps_reg_M'] = test['ps_reg_03'].apply(lambda x: recon(x)[1])
test['ps_reg_A'].replace(19,-1, inplace=True)
test['ps_reg_M'].replace(51,-1, inplace=True)


### Froza's baseline
### Froza's baseline
### Froza's baseline
### Froza's baseline

d_median = train.median(axis=0)
d_mean = train.mean(axis=0)
d_skew = train.skew(axis=0)
one_hot = {c: list(train[c].unique()) for c in train.columns if c not in ['id','target']}

def transform_df(df):
    df = pd.DataFrame(df)
    dcol = [c for c in df.columns if c not in ['id','target']]
    df['ps_car_13_x_ps_reg_03'] = df['ps_car_13'] * df['ps_reg_03']
    df['negative_one_vals'] = np.sum((df[dcol]==-1).values, axis=1)
    for c in dcol:
        if '_bin' not in c: #standard arithmetic
            df[c+str('_median_range')] = (df[c].values > d_median[c]).astype(np.int)
            df[c+str('_mean_range')] = (df[c].values > d_mean[c]).astype(np.int)

    for c in one_hot:
        if len(one_hot[c])>2 and len(one_hot[c]) < 7:
            for val in one_hot[c]:
                df[c+'_oh_' + str(val)] = (df[c].values == val).astype(np.int)
    return df

def multi_transform(df):
    print('Init Shape: ', df.shape)
    p = Pool(cpu_count())
    df = p.map(transform_df, np.array_split(df, cpu_count()))
    df = pd.concat(df, axis=0, ignore_index=True).reset_index(drop=True)
    p.close(); p.join()
    print('After Shape: ', df.shape)
    return df

train = multi_transform(train)
test = multi_transform(test)



### Gini

def ginic(actual, pred):
    actual = np.asarray(actual)
    n = len(actual)
    a_s = actual[np.argsort(pred)]
    a_c = a_s.cumsum()
    giniSum = a_c.sum() / a_s.sum() - (n + 1) / 2.0
    return giniSum / n

def gini_normalized(a, p):
    if p.ndim == 2:
        p = p[:,1]
    return ginic(a, p) / ginic(a, a)


def gini_xgb(preds, dtrain):
    labels = dtrain.get_label()
    gini_score = gini_normalized(labels, preds)
    return 'gini', gini_score

# %%
### XGB modeling

sub = pd.DataFrame()
sub['id'] = testid
sub['target'] =0

params = {'eta': 0.025, 'max_depth': 4,
          'subsample': 0.9, 'colsample_bytree': 0.7,
          'colsample_bylevel':0.7,
            'min_child_weight':100,
            'alpha':4,
            'objective': 'binary:logistic', 'eval_metric': 'auc', 'seed': 99, 'silent': True}

X = train.values
X_1 = test.values
nrounds=10**6  # need to change to 2000
kfold = 4  # need to change to 5
skf = StratifiedKFold(n_splits=kfold, random_state=99)
for i, (train_index, test_index) in enumerate(skf.split(X, y)):
    print(' xgb kfold: {}  of  {} : '.format(i+1, kfold))
    X_train, X_valid = X[train_index], X[test_index]
    y_train, y_valid = y[train_index], y[test_index]
    d_train = xgb.DMatrix(X_train, y_train)
    d_valid = xgb.DMatrix(X_valid, y_valid)
    watchlist = [(d_train, 'train'), (d_valid, 'valid')]
    xgb_model = xgb.train(params, d_train, nrounds, watchlist, early_stopping_rounds=70,
                          feval=gini_xgb, maximize=True, verbose_eval=100)
    sub['target'] += xgb_model.predict(xgb.DMatrix(X_1),
                        ntree_limit=xgb_model.best_ntree_limit+50) / (kfold)

    # train_pred = xgb_model.predict(xgb.DMatrix(X.iloc[test_index].values),
    #                     ntree_limit=xgb_model.best_ntree_limit+50)
    # sub_train['target'].iloc[test_index] = train_pred
# cv record: 0.2819, 0.2886, 0.2820, 0.2906, 0.2773

gc.collect()
sub.head(2)

sub.to_csv(base_path+'test_kueipo_v2.csv', index=False, float_format='%.5f')

gini_l = [0.2908, 0.2834, 0.2855, 0.2765] # 用kueipo给的超参
sum(gini_l)/len(gini_l)
# gini_l = [0.290823,0.283362,0.285549,0.27651] # 没调参的结果
# sum(gini_l)/len(gini_l)



# ### Submission
#
# sub['target'] = model.predict(xgb.DMatrix(test), ntree_limit=model.best_ntree_limit)
# sub.to_csv(base_path + 'output_kueipo.csv',index=False)
