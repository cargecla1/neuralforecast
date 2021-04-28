# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/experiments__utils.ipynb (unless otherwise specified).

__all__ = ['ENV_VARS', 'get_mask_dfs', 'get_random_mask_dfs', 'scale_data', 'create_datasets', 'instantiate_loaders',
           'instantiate_nbeats', 'instantiate_esrnn', 'instantiate_mqesrnn', 'instantiate_model', 'model_fit_predict',
           'evaluate_model', 'hyperopt_tunning']

# Cell
ENV_VARS = dict(OMP_NUM_THREADS='2',
                OPENBLAS_NUM_THREADS='2',
                MKL_NUM_THREADS='3',
                VECLIB_MAXIMUM_THREADS='2',
                NUMEXPR_NUM_THREADS='3')

# Cell
import os
# Limit number of threads in numpy and others to avoid throttling
os.environ.update(ENV_VARS)
import random
import time
from functools import partial

import numpy as np
import pandas as pd
from hyperopt import fmin, tpe, hp, Trials, STATUS_OK

from ..data.scalers import Scaler
from ..data.tsdataset import TimeSeriesDataset
from ..data.tsloader_general import TimeSeriesLoader
from ..models.esrnn.esrnn import ESRNN
from ..models.esrnn.mqesrnn import MQESRNN
from ..models.nbeats.nbeats import Nbeats

# Cell
def get_mask_dfs(Y_df, ds_in_val, ds_in_test):
    # train mask
    train_mask_df = Y_df.copy()[['unique_id', 'ds']]
    train_mask_df.sort_values(by=['unique_id', 'ds'], inplace=True)
    train_mask_df.reset_index(drop=True, inplace=True)

    train_mask_df['sample_mask'] = 1
    train_mask_df['available_mask'] = 1

    idx_out = train_mask_df.groupby('unique_id').tail(ds_in_val+ds_in_test).index
    train_mask_df.loc[idx_out, 'sample_mask'] = 0

    # test mask
    test_mask_df = train_mask_df.copy()
    test_mask_df['sample_mask'] = 0
    idx_test = test_mask_df.groupby('unique_id').tail(ds_in_test).index
    test_mask_df.loc[idx_test, 'sample_mask'] = 1

    # validation mask
    val_mask_df = train_mask_df.copy()
    val_mask_df['sample_mask'] = 1
    val_mask_df['sample_mask'] = val_mask_df['sample_mask'] - train_mask_df['sample_mask']
    val_mask_df['sample_mask'] = val_mask_df['sample_mask'] - test_mask_df['sample_mask']

    assert len(train_mask_df)==len(Y_df), \
        f'The mask_df length {len(train_mask_df)} is not equal to Y_df length {len(Y_df)}'

    return train_mask_df, val_mask_df, test_mask_df

# Cell
def get_random_mask_dfs(Y_df, ds_in_test,
                        n_val_windows, n_ds_val_window,
                        n_uids, freq):
    """
    Generates train, test and random validation mask.
    Train mask begins by avoiding ds_in_test

    Validation mask: 1) samples n_uids unique ids
                     2) creates windows of size n_ds_val_window
    Parameters
    ----------
    ds_in_test: int
        Number of ds in test.
    n_uids: int
        Number of unique ids in validation.
    n_val_windows: int
        Number of windows for validation.
    n_ds_val_window: int
        Number of ds in each validation window.
    periods: int
        ds_in_test multiplier.
    freq: str
        string that determines datestamp frequency, used in
        random windows creation.
    """
    np.random.seed(1)
    #----------------------- Train mask -----------------------#
    # Initialize masks
    train_mask_df, val_mask_df, test_mask_df = get_mask_dfs(Y_df=Y_df,
                                                            ds_in_val=0,
                                                            ds_in_test=ds_in_test)

    assert val_mask_df['sample_mask'].sum()==0, 'Muerte'

    #----------------- Random Validation mask -----------------#
    # Overwrite validation with random windows
    uids = train_mask_df['unique_id'].unique()
    val_uids = np.random.choice(uids, n_uids, replace=False)

    # Validation avoids test
    idx_test = train_mask_df.groupby('unique_id').tail(ds_in_test).index
    available_ds = train_mask_df.loc[~train_mask_df.index.isin(idx_test)]['ds'].unique()
    val_init_ds = np.random.choice(available_ds, n_val_windows, replace=False)

    # Creates windows
    val_ds = [pd.date_range(init, periods=n_ds_val_window, freq=freq) for init in val_init_ds]
    val_ds = np.concatenate(val_ds)

    # Cleans random windows from train mask
    val_idx = train_mask_df.query('unique_id in @val_uids & ds in @val_ds').index
    train_mask_df.loc[val_idx, 'sample_mask'] = 0
    val_mask_df.loc[val_idx, 'sample_mask'] = 1

    return train_mask_df, val_mask_df, test_mask_df

# Cell
def scale_data(Y_df, X_df, mask_df, normalizer_y, normalizer_x):
    mask = mask_df['available_mask'].values * mask_df['sample_mask'].values

    if normalizer_y is not None:
        scaler_y = Scaler(normalizer=normalizer_y)
        Y_df['y'] = scaler_y.scale(x=Y_df['y'].values, mask=mask)
    else:
        scaler_y = None

    if normalizer_x is not None:
        X_cols = [col for col in X_df.columns if col not in ['unique_id','ds']]
        for col in X_cols:
            scaler_x = Scaler(normalizer=normalizer_x)
            X_df[col] = scaler_x.scale(x=X_df[col].values, mask=mask)

    return Y_df, X_df, scaler_y

# Cell
def create_datasets(mc, S_df, Y_df, X_df, f_cols,
                    ds_in_test, ds_in_val,
                    n_uids, n_val_windows, freq,
                    is_val_random):
    #------------------------------------- Available and Validation Mask ------------------------------------#
    if is_val_random:
        train_mask_df, val_mask_df, test_mask_df = get_random_mask_dfs(Y_df=Y_df,
                                                                       ds_in_test=ds_in_test,
                                                                       n_uids=n_uids,
                                                                       n_val_windows=n_val_windows,
                                                                       n_ds_val_window=ds_in_val//n_val_windows,
                                                                       freq=freq)
    else:
        train_mask_df, val_mask_df, test_mask_df = get_mask_dfs(Y_df=Y_df,
                                                                ds_in_test=ds_in_test,
                                                                ds_in_val=ds_in_val)

    #---------------------------------------------- Scale Data ----------------------------------------------#
    Y_df, X_df, scaler_y = scale_data(Y_df=Y_df, X_df=X_df, mask_df=train_mask_df,
                                      normalizer_y=mc['normalizer_y'], normalizer_x=mc['normalizer_x'])

    #----------------------------------------- Declare Dataset and Loaders ----------------------------------#
    train_dataset = TimeSeriesDataset(S_df=S_df, Y_df=Y_df, X_df=X_df, mask_df=train_mask_df, f_cols=f_cols, verbose=True)
    val_dataset   = TimeSeriesDataset(S_df=S_df, Y_df=Y_df, X_df=X_df, mask_df=val_mask_df, f_cols=f_cols, verbose=True)
    test_dataset  = TimeSeriesDataset(S_df=S_df, Y_df=Y_df, X_df=X_df, mask_df=test_mask_df, f_cols=f_cols, verbose=True)

    if ds_in_test == 0:
        test_dataset = None

    return train_dataset, val_dataset, test_dataset, scaler_y

# Cell
def instantiate_loaders(mc, train_dataset, val_dataset, test_dataset):
    train_loader = TimeSeriesLoader(ts_dataset=train_dataset,
                                    model=mc['model'],
                                    window_sampling_limit=int(mc['window_sampling_limit']),
                                    input_size=int(mc['input_size_multiplier']*mc['output_size']),
                                    output_size=int(mc['output_size']),
                                    idx_to_sample_freq=int(mc['idx_to_sample_freq']),
                                    len_sample_chunks=mc['len_sample_chunks'],
                                    batch_size=int(mc['batch_size']),
                                    n_series_per_batch=mc['n_series_per_batch'],
                                    complete_inputs=mc['complete_inputs'],
                                    complete_sample=mc['complete_sample'],
                                    shuffle=True)
    if val_dataset is not None:
        val_loader = TimeSeriesLoader(ts_dataset=val_dataset,
                                      model=mc['model'],
                                      window_sampling_limit=int(mc['window_sampling_limit']),
                                      input_size=int(mc['input_size_multiplier']*mc['output_size']),
                                      output_size=int(mc['output_size']),
                                      idx_to_sample_freq=int(mc['val_idx_to_sample_freq']),
                                      len_sample_chunks=mc['len_sample_chunks'],
                                      batch_size=1,
                                      n_series_per_batch=mc['n_series_per_batch'],
                                      complete_inputs=mc['complete_inputs'],
                                      complete_sample=True,
                                      shuffle=False)

    else:
        val_loader = None

    if test_dataset is not None:
        test_loader = TimeSeriesLoader(ts_dataset=test_dataset,
                                       model=mc['model'],
                                       window_sampling_limit=int(mc['window_sampling_limit']),
                                       input_size=int(mc['input_size_multiplier']*mc['output_size']),
                                       output_size=int(mc['output_size']),
                                       idx_to_sample_freq=mc['val_idx_to_sample_freq'],
                                       len_sample_chunks=mc['len_sample_chunks'],
                                       batch_size=1,
                                       n_series_per_batch=mc['n_series_per_batch'],
                                       complete_inputs=False,
                                       complete_sample=False, #TODO: this may be true by default, think interaction with sample_freq
                                       shuffle=False)
    else:
        test_loader = None

    return train_loader, val_loader, test_loader

# Cell
def instantiate_nbeats(mc):
    mc['n_hidden_list'] = len(mc['stack_types']) * [ mc['n_layers'][0]*[int(mc['n_hidden'])] ]
    model = Nbeats(input_size_multiplier=mc['input_size_multiplier'],
                   output_size=int(mc['output_size']),
                   shared_weights=mc['shared_weights'],
                   initialization=mc['initialization'],
                   activation=mc['activation'],
                   stack_types=mc['stack_types'],
                   n_blocks=mc['n_blocks'],
                   n_layers=mc['n_layers'],
                   n_hidden=mc['n_hidden_list'],
                   n_harmonics=int(mc['n_harmonics']),
                   n_polynomials=int(mc['n_polynomials']),
                   x_s_n_hidden=int(mc['x_s_n_hidden']),
                   exogenous_n_channels=int(mc['exogenous_n_channels']),
                   batch_normalization = mc['batch_normalization'],
                   dropout_prob_theta=mc['dropout_prob_theta'],
                   dropout_prob_exogenous=mc['dropout_prob_exogenous'],
                   learning_rate=float(mc['learning_rate']),
                   lr_decay=float(mc['lr_decay']),
                   n_lr_decay_steps=float(mc['n_lr_decay_steps']),
                   weight_decay=mc['weight_decay'],
                   l1_theta=mc['l1_theta'],
                   n_iterations=int(mc['n_iterations']),
                   early_stopping=int(mc['early_stopping']),
                   loss=mc['loss'],
                   loss_hypar=float(mc['loss_hypar']),
                   val_loss=mc['val_loss'],
                   frequency=mc['frequency'],
                   seasonality=int(mc['seasonality']),
                   random_seed=int(mc['random_seed']),
                   device=mc['device'])
    return model

# Cell
def instantiate_esrnn(mc):
    model = ESRNN(# Architecture parameters
                  input_size=int(mc['input_size_multiplier']*mc['output_size']),
                  output_size=int(mc['output_size']),
                  es_component=mc['es_component'],
                  cell_type=mc['cell_type'],
                  state_hsize=int(mc['state_hsize']),
                  dilations=mc['dilations'],
                  add_nl_layer=mc['add_nl_layer'],
                  # Optimization parameters
                  n_iterations=int(mc['n_iterations']),
                  early_stopping=int(mc['early_stopping']),
                  learning_rate=mc['learning_rate'],
                  lr_scheduler_step_size=int(mc['lr_scheduler_step_size']),
                  lr_decay=mc['lr_decay'],
                  per_series_lr_multip=mc['per_series_lr_multip'],
                  gradient_eps=mc['gradient_eps'],
                  gradient_clipping_threshold=mc['gradient_clipping_threshold'],
                  rnn_weight_decay=mc['rnn_weight_decay'],
                  noise_std=mc['noise_std'],
                  level_variability_penalty=mc['level_variability_penalty'],
                  testing_percentile=mc['testing_percentile'],
                  training_percentile=mc['training_percentile'],
                  loss=mc['loss'],
                  val_loss=mc['val_loss'],
                  seasonality=mc['seasonality'],
                  random_seed=int(mc['random_seed'])
                  # Data parameters
                  )
    return model

# Cell
def instantiate_mqesrnn(mc):
    model = MQESRNN(# Architecture parameters
                    input_size=int(mc['input_size_multiplier']*mc['output_size']),
                    output_size=int(mc['output_size']),
                    es_component=mc['es_component'],
                    cell_type=mc['cell_type'],
                    state_hsize=int(mc['state_hsize']),
                    dilations=mc['dilations'],
                    add_nl_layer=mc['add_nl_layer'],
                    # Optimization parameters
                    n_iterations=int(mc['n_iterations']),
                    early_stopping=int(mc['early_stopping']),
                    learning_rate=mc['learning_rate'],
                    lr_scheduler_step_size=int(mc['lr_scheduler_step_size']),
                    lr_decay=mc['lr_decay'],
                    gradient_eps=mc['gradient_eps'],
                    gradient_clipping_threshold=mc['gradient_clipping_threshold'],
                    rnn_weight_decay=mc['rnn_weight_decay'],
                    noise_std=mc['noise_std'],
                    testing_percentiles=list(mc['testing_percentiles']),
                    training_percentiles=list(mc['training_percentiles']),
                    loss=mc['loss'],
                    val_loss=mc['val_loss'],
                    random_seed=int(mc['random_seed'])
                    # Data parameters
                  )
    return model

# Cell
def instantiate_model(mc):
    MODEL_DICT = {'nbeats': instantiate_nbeats,
                  'esrnn': instantiate_esrnn,
                  'new_rnn': instantiate_esrnn,
                  'mqesrnn': instantiate_mqesrnn,}
    return MODEL_DICT[mc['model']](mc)

# Cell
def model_fit_predict(mc, S_df, Y_df, X_df, f_cols,
                      ds_in_test, ds_in_val,
                      n_uids, n_val_windows, freq,
                      is_val_random):

    # Protect inplace modifications
    Y_df = Y_df.copy()
    if X_df is not None:
        X_df = X_df.copy()
    if S_df is not None:
        S_df = S_df.copy()

    #----------------------------------------------- Datasets -----------------------------------------------#
    train_dataset, val_dataset, test_dataset, scaler_y = create_datasets(mc=mc,
                                                                         S_df=S_df, Y_df=Y_df, X_df=X_df,
                                                                         f_cols=f_cols,
                                                                         ds_in_test=ds_in_test,
                                                                         ds_in_val=ds_in_val,
                                                                         n_uids=n_uids,
                                                                         n_val_windows=n_val_windows,
                                                                         freq=freq, is_val_random=is_val_random)

    #------------------------------------------- Instantiate & fit -------------------------------------------#
    train_loader, val_loader, test_loader = instantiate_loaders(mc=mc,
                                                                train_dataset=train_dataset,
                                                                val_dataset=val_dataset,
                                                                test_dataset=test_dataset)
    model = instantiate_model(mc=mc)
    model.fit(train_ts_loader=train_loader, val_ts_loader=val_loader, verbose=True, eval_freq=mc['eval_freq'])

    #------------------------------------------------ Predict ------------------------------------------------#
    # Predict test if available
    if ds_in_test > 0:
        y_true, y_hat, mask = model.predict(ts_loader=test_loader, return_decomposition=False)
        meta_data = test_loader.ts_dataset.meta_data
    else:
        y_true, y_hat, mask = model.predict(ts_loader=val_loader, return_decomposition=False)
        meta_data = val_loader.ts_dataset.meta_data

    # Scale to original scale
    if mc['normalizer_y'] is not None:
        y_true_shape = y_true.shape
        y_true = scaler_y.inv_scale(x=y_true.flatten())
        y_true = np.reshape(y_true, y_true_shape)

        y_hat = scaler_y.inv_scale(x=y_hat.flatten())
        y_hat = np.reshape(y_hat, y_true_shape)

    print(f"y_true.shape (#n_series, #n_fcds, #lt): {y_true.shape}")
    print(f"y_hat.shape (#n_series, #n_fcds, #lt): {y_hat.shape}")
    print("\n")
    return y_true, y_hat, mask, meta_data, model

# Cell
def evaluate_model(mc, loss_function,
                   S_df, Y_df, X_df, f_cols,
                   ds_in_test, ds_in_val,
                   n_uids, n_val_windows, freq,
                   is_val_random,
                   loss_kwargs):

    print(47*'=' + '\n')
    print(pd.Series(mc))
    print(47*'=' + '\n')

    # Some asserts due to work in progress
    n_series = Y_df['unique_id'].nunique()
    if n_series > 1:
        assert mc['normalizer_y'] is None, 'Data scaling not implemented with multiple time series'
        assert mc['normalizer_x'] is None, 'Data scaling not implemented with multiple time series'

    assert ds_in_test % mc['val_idx_to_sample_freq']==0, 'outsample size should be multiple of val_idx_to_sample_freq'

    # Make predictions
    start = time.time()
    y_true, y_hat, mask, meta_data, model = model_fit_predict(mc=mc,
                                                              S_df=S_df,
                                                              Y_df=Y_df,
                                                              X_df=X_df,
                                                              f_cols=f_cols,
                                                              ds_in_test=ds_in_test,
                                                              ds_in_val=ds_in_val,
                                                              n_uids=n_uids,
                                                              n_val_windows=n_val_windows,
                                                              freq=freq,
                                                              is_val_random=is_val_random)
    run_time = time.time() - start

    # Evaluate predictions
    loss = loss_function(y=y_true, y_hat=y_hat, weights=mask, **loss_kwargs)

    result =  {'loss': loss,
               'mc': mc,
               'y_true': y_true,
               'y_hat': y_hat,
               'trajectories': model.trajectories,
               'run_time': run_time,
               'status': STATUS_OK}
    return result

# Cell
def hyperopt_tunning(space, hyperopt_max_evals, loss_function,
                     S_df, Y_df, X_df, f_cols,
                     ds_in_val,
                     n_uids, n_val_windows, freq,
                     is_val_random,
                     save_trials=False,
                     loss_kwargs=None):
    trials = Trials()
    fmin_objective = partial(evaluate_model, loss_function=loss_function,
                             S_df=S_df, Y_df=Y_df, X_df=X_df, f_cols=f_cols,
                             ds_in_test=0, ds_in_val=ds_in_val,
                             n_uids=n_uids, n_val_windows=n_val_windows, freq=freq,
                             is_val_random=is_val_random,
                             loss_kwargs=loss_kwargs or {})

    fmin(fmin_objective, space=space, algo=tpe.suggest, max_evals=hyperopt_max_evals, trials=trials, verbose=True)

    return trials