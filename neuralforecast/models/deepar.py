# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/models.deepar.ipynb.

# %% auto 0
__all__ = ['DeepAR']

# %% ../../nbs/models.deepar.ipynb 6
from typing import List

import torch
import torch.nn as nn

from ..losses.pytorch import MAE, StudentTLoss
from ..common._base_recurrent import BaseRecurrent
from ..common._modules import MLP, TemporalConvolutionEncoder

# %% ../../nbs/models.deepar.ipynb 8
class StaticCovariateEncoder(nn.Module):
    def __init__(self, in_features, out_features):
        super(StaticCovariateEncoder, self).__init__()
        layers = [nn.Dropout(p=0.5),
                  nn.Linear(in_features=in_features, 
                            out_features=out_features),
                  nn.ReLU()]
        self.encoder = nn.Sequential(*layers)

    def forward(self, x, repeats):        
        # Apply Static Encoder and repeat data.
        # [N,S_in] -> [N,S_out] -> [N,T,S_out]
        x = self.encoder(x)
        x = x.unsqueeze(1).repeat(1, repeats, 1)
        return x

# %% ../../nbs/models.deepar.ipynb 9
class DeepAR(BaseRecurrent):
    """ DeepAR1

    The DeepAR architecture produces predictive distributions based on
    an autoreggresive recurrent neural network, its outputs use a multi-step 
    recurrent forecasting strategy.

    **Parameters:**<br>
    `h`: int, forecast horizon.<br>
    `input_size`: int, maximum sequence length for truncated train backpropagation. Default -1 uses all history.<br>
    `stat_hidden_size`: int, dimension of the embedding space for each static feature.<br>
    `encoder_n_layers`: int=2, number of layers for the LSTM.<br>
    `encoder_hidden_size`: int=200, units for the LSTM's hidden state size.<br>
    `encoder_activation`: str=`tanh`, type of LSTM activation from `tanh` or `relu`.<br>
    `encoder_bias`: bool=True, whether or not to use biases b_ih, b_hh within LSTM units.<br>
    `encoder_dropout`: float=0., dropout regularization applied to LSTM outputs.<br>
    `context_size`: int=10, size of context vector for each timestamp on the forecasting window.<br>
    `decoder_hidden_size`: int=200, size of hidden layer for the MLP decoder.<br>
    `decoder_layers`: int=2, number of layers for the MLP decoder.<br>
    `futr_exog_list`: str list, future exogenous columns.<br>
    `hist_exog_list`: str list, historic exogenous columns.<br>
    `stat_exog_list`: str list, static exogenous columns.<br>
    `loss`: PyTorch module, instantiated train loss class from [losses collection](https://nixtla.github.io/neuralforecast/losses.pytorch.html).<br>
    `learning_rate`: float=1e-3, initial optimization learning rate (0,1).<br>
    `batch_size`: int=32, number of differentseries in each batch.<br>
    `scaler_type`: str='robust', type of scaler for temporal inputs normalization see [temporal scalers](https://nixtla.github.io/neuralforecast/common.scalers.html).<br>
    `random_seed`: int=1, random_seed for pytorch initializer and numpy generators.<br>
    `num_workers_loader`: int=os.cpu_count(), workers to be used by `TimeSeriesDataLoader`.<br>
    `drop_last_loader`: bool=False, if True `TimeSeriesDataLoader` drops last non-full batch.<br>
    `**trainer_kwargs`: int,  keyword trainer arguments inherited from [PyTorch Lighning's trainer](https://pytorch-lightning.readthedocs.io/en/stable/api/pytorch_lightning.trainer.trainer.Trainer.html?highlight=trainer).<br>    
    """
    def __init__(self,
                 h: int,
                 input_size: int = -1,
                 stat_hidden_size: int = 10,
                 encoder_n_layers: int = 2,
                 encoder_hidden_size: int = 200,
                 encoder_bias: bool = True,
                 encoder_dropout: float = 0.,
                 context_size: int = 10,
                 decoder_hidden_size: int = 200,
                 decoder_layers: int = 2,
                 futr_exog_list = None,
                 hist_exog_list = None,
                 stat_exog_list = None,
                 loss = StudentTLoss(level=[80, 90]),
                 learning_rate: float = 1e-3,
                 batch_size=32,
                 scaler_type: str='robust',
                 random_seed=1,
                 num_workers_loader=0,
                 drop_last_loader=False,
                 **trainer_kwargs):
        super(DeepAR, self).__init__(
            h = h,
            input_size = input_size,
            loss=loss,
            learning_rate = learning_rate,
            batch_size=batch_size,
            scaler_type=scaler_type,
            futr_exog_list=futr_exog_list,
            hist_exog_list=hist_exog_list,
            stat_exog_list=stat_exog_list,
            num_workers_loader=num_workers_loader,
            drop_last_loader=drop_last_loader,
            random_seed=random_seed,
            **trainer_kwargs
        )
        #---------------------------------------- Parsing dimensions --------------------------------------#
        # Parsing input dimensions
        self.futr_exog_size = len(self.futr_exog_list)
        self.hist_exog_size = len(self.hist_exog_list)
        self.stat_exog_size = len(self.stat_exog_list)
        self.stat_hidden_size = stat_hidden_size if self.stat_exog_size>0 else 0

        # LSTM
        self.encoder_n_layers = encoder_n_layers
        self.encoder_hidden_size = encoder_hidden_size
        self.encoder_bias = encoder_bias
        self.encoder_dropout = encoder_dropout

        # Context adapter
        self.context_size = context_size

        # MLP decoder
        self.decoder_hidden_size = decoder_hidden_size
        self.decoder_layers = decoder_layers

        # LSTM input size (1 for target variable y)
        input_encoder = 1 + self.hist_exog_size + self.stat_hidden_size

        #-------------------------------------- Instantiate Components ------------------------------------#
        # Instantiate model components
        self.stat_encoder = StaticCovariateEncoder(
                                            in_features=self.stat_exog_size,
                                            out_features=stat_hidden_size)
#         self.hist_encoder = nn.LSTM(input_size=input_encoder,
#                                     hidden_size=self.encoder_hidden_size,
#                                     num_layers=self.encoder_n_layers,
#                                     bias=self.encoder_bias,
#                                     dropout=self.encoder_dropout,
#                                     batch_first=True)
        
        # Instantiate historic encoder
        self.hist_encoder = TemporalConvolutionEncoder(
                                   in_channels=input_encoder,
                                   out_channels=self.encoder_hidden_size,
                                   kernel_size=2, # Almost like lags
                                   dilations=[1,2,4,8,16],
                                   activation='ReLU')

        # Context adapter
        self.context_adapter = nn.Linear(in_features=self.encoder_hidden_size + self.futr_exog_size * h,
                                         out_features=self.context_size * h)

        # Decoder MLP
        self.mlp_decoder = MLP(in_features=self.context_size + self.futr_exog_size,
                              out_features=self.decoder_hidden_size,
                              hidden_size=self.decoder_hidden_size,
                              num_layers=self.decoder_layers,
                              activation='ReLU',
                              dropout=0.0)
        self.adapter = loss.get_adapter(in_features=decoder_hidden_size)
        
#         # Decoder MLP
#         self.mlp_decoder = MLP(in_features=self.context_size + self.futr_exog_size,
#                                out_features=self.loss.outputsize_multiplier,
#                                hidden_size=self.decoder_hidden_size,
#                                num_layers=self.decoder_layers,
#                                activation='ReLU',
#                                dropout=0.0)        

    def forward(self, windows_batch):

        # Parse windows_batch
        encoder_input = windows_batch['insample_y'] # [B, seq_len, 1]
        futr_exog     = windows_batch['futr_exog']
        hist_exog     = windows_batch['hist_exog']
        stat_exog     = windows_batch['stat_exog']

        # Concatenate y, historic and static inputs
        # [B, C, seq_len, 1] -> [B, seq_len, C]
        # Contatenate [ Y_t, | X_{t-L},..., X_{t} | S ]
        batch_size, seq_len = encoder_input.shape[:2]
        if self.hist_exog_size > 0:
            hist_exog = hist_exog.permute(0,2,1,3).squeeze(-1) # [B, X, seq_len, 1] -> [B, seq_len, X]
            encoder_input = torch.cat((encoder_input, hist_exog), dim=2)

        if self.stat_exog_size > 0:
            stat_hidden = self.stat_encoder(x=stat_exog, repeats=seq_len)  # [B, seq_len, S_out]
            encoder_input = torch.cat((encoder_input, stat_hidden), dim=2)

        # RNN forward
        #hidden_state, _ = self.hist_encoder(encoder_input) # [B, seq_len, rnn_hidden_state]
        hidden_state = self.hist_encoder(encoder_input) # [B, seq_len, rnn_hidden_state]

        if self.futr_exog_size > 0:
            futr_exog = futr_exog.permute(0,2,3,1)[:,:,1:,:]  # [B, F, seq_len, 1+H] -> [B, seq_len, H, F]
            hidden_state = torch.cat(( hidden_state, futr_exog.reshape(batch_size, seq_len, -1)), dim=2)

        # Context adapter
        context = self.context_adapter(hidden_state)
        context = context.reshape(batch_size, seq_len, self.h, self.context_size)

        # Residual connection with futr_exog
        if self.futr_exog_size > 0:
            context = torch.cat((context, futr_exog), dim=-1)

        # Final forecast
        hidden = self.mlp_decoder(context)
        output = self.adapter(hidden) # Adapt + Domain map

        return output
