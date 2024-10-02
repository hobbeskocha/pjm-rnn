# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: light
#       format_version: '1.5'
#       jupytext_version: 1.16.4
#   kernelspec:
#     display_name: rnn
#     language: python
#     name: python3
# ---

# # NYC: Modeling

# ## Libraries

# +
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import random

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, root_mean_squared_error
# -

torch.manual_seed(13)
random.seed(13)
np.random.seed(13)

# ## Import Data

# +
nyc_train = pd.read_csv("../data/nyc_ny_train_hourly_interpolated.csv", index_col= "UTC_Timestamp")
nyc_test = pd.read_csv("../data/nyc_ny_test_hourly.csv", index_col= "UTC_Timestamp")

nyc_train.columns = ["Actual_Load_MW"]
nyc_test.columns = ["Actual_Load_MW"]
# -

nyc_train.info()
nyc_train.head()

nyc_test.info()
nyc_test.head()


# ## Data Processing

# ### Class Definitions

# +
class ElectricLoadDataset(Dataset):
    def __init__(self, df, seq_len = 24):
        self.seq_len = seq_len
        self.data = df["Actual_Load_MW"].values.reshape(-1, 1)

    def __len__(self):
        return len(self.data) - self.seq_len
    
    def __getitem__(self, idx):
        x = self.data[idx:idx + self.seq_len]
        y = self.data[idx + self.seq_len]
        return torch.tensor(x, dtype = torch.float32), torch.tensor(y, dtype = torch.float32)
    
class LSTMModel(nn.Module):
    def __init__(self, input, hidden, n_layers, dropout_prob = 0.1):
        super(LSTMModel, self).__init__()
        self.hidden_size = hidden
        self.num_layers = n_layers
        self.lstm = nn.LSTM(
            input_size = input,
            hidden_size = hidden,
            num_layers = n_layers,
            batch_first = True,
            dropout = dropout_prob
        )
        self.dropout = nn.Dropout(dropout_prob)
        self.fc = nn.Linear(hidden, 1)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)

        out, _ = self.lstm(x, (h0, c0))
        out = self.dropout(out[:, -1, :])
        out = self.fc(out)
        return out


# -

# ### Model Training

def init_weights(m):
    if isinstance(m, nn.Linear):
        # Apply Xavier initialization to Linear layers
        nn.init.xavier_uniform_(m.weight)
    elif isinstance(m, nn.LSTM):
        # Apply Xavier initialization to LSTM weight matrices
        for name, param in m.named_parameters():
            if 'weight_ih' in name:
                nn.init.xavier_uniform_(param.data)
            elif 'weight_hh' in name:
                nn.init.orthogonal_(param.data)
            elif 'bias' in name:
                param.data.fill_(0)


# +
sequence_length = 24
batch_size = 32
input_size = 1
hidden_size = 50
num_layers = 2
dropout_probability = 0.1
epochs = 20

normalizer = MinMaxScaler(feature_range=(0, 1))
nyc_train_normalized = nyc_train.copy()
nyc_train_normalized["Actual_Load_MW"] = normalizer.fit_transform(nyc_train_normalized)

train_elec_dataset = ElectricLoadDataset(nyc_train_normalized, sequence_length)
train_dataloader = DataLoader(train_elec_dataset, batch_size = batch_size, shuffle = False)

lstm = LSTMModel(input_size, hidden_size, num_layers, dropout_probability)
lstm.apply(init_weights)
criterion = nn.MSELoss()
optimizer = optim.Adam(lstm.parameters(), lr = 0.0001)
losses = list()
# -

lstm.train()
for epoch in range(epochs):
    for x, y in train_dataloader:
        x = x.view(x.size(0), sequence_length, input_size)
        optimizer.zero_grad()
        output = lstm(x)
        loss = criterion(output, y.view(-1, 1))
        loss.backward()
        nn.utils.clip_grad_norm_(lstm.parameters(), max_norm = 1.0)
        optimizer.step()

    print(f"Epoch {epoch + 1} with loss: {loss.item()}")
    losses.append(loss.item())

loss_df = pd.DataFrame({"epoch": list(range(epochs)), "loss": losses})
sns.lineplot(loss_df, x = "epoch", y = "loss")
plt.xlabel("Epochs")
plt.ylabel("MSE Loss")
plt.show()

# ### Model Inference

# +
nyc_test_normalized = nyc_test.copy()
nyc_test_normalized["Actual_Load_MW"] = normalizer.transform(nyc_test_normalized)
nyc_test_normalized = nyc_test_normalized.ffill(axis = 0)

test_elec_dataset = ElectricLoadDataset(nyc_test_normalized, sequence_length)
test_dataloader = DataLoader(test_elec_dataset, batch_size = batch_size, shuffle  = False)
predictions, actuals = list(), list()
mse = nn.MSELoss()
total_mse = 0
n_samples = 0

lstm.eval()
with torch.no_grad():
    for seq, label in test_dataloader:
        seq = seq.view(seq.size(0), sequence_length, input_size)
        output = lstm(seq)

        predictions.append(output.numpy())
        actuals.append(label.numpy())
        
        loss = mse(output, label)
        total_mse += loss.item() * seq.size(0)
        n_samples += seq.size(0)

avg_mse = total_mse / n_samples
print("Test MSE: ", avg_mse)

predictions = np.concatenate(predictions, axis = 0)
actuals = np.concatenate(actuals, axis = 0)
predictions = normalizer.inverse_transform(predictions)
actuals = normalizer.inverse_transform(actuals)

# +
actuals = pd.Series(actuals.squeeze())
predictions = pd.Series(predictions.squeeze())

data = {"Predicted_Load": predictions,
        "Actual_Load": actuals}

model_predictions = pd.concat(data, axis = 1)
model_predictions.index = nyc_test[24:].index
model_predictions
# -

model_predictions.isna().sum()

model_mse = mean_squared_error(model_predictions["Actual_Load"], model_predictions["Predicted_Load"])
model_rmse = root_mean_squared_error(model_predictions["Actual_Load"], model_predictions["Predicted_Load"])
print(model_mse, model_rmse)

nyc_predictions = pd.merge(nyc_test[24:], model_predictions, on = "UTC_Timestamp")
nyc_predictions

# +
nyc_predictions.index = pd.to_datetime(nyc_predictions.index)
nyc_predictions_daily = nyc_predictions.resample("D").sum()

sns.lineplot(nyc_predictions, x = nyc_predictions.index, y = "Predicted_Load", label = "Predicted")
sns.lineplot(nyc_predictions, x = nyc_predictions.index, y = "Actual_Load_MW", label = "Actual", alpha = 0.7)
plt.xticks(rotation = 45)
plt.show()
# -

nyc_predictions.drop(["Actual_Load_MW"], axis = 1, inplace = True)
nyc_predictions["month"] = nyc_predictions.index.month
nyc_predictions["hour"] = nyc_predictions.index.hour

# +
sns.boxplot(nyc_predictions, x = "month", y = "Predicted_Load").set(title = "NYC Predicted Load by Month")
plt.show()

sns.boxplot(nyc_predictions, x = "month", y = "Actual_Load").set(title = "NYC Actual Load by Month")
plt.show()

# +
sns.boxplot(nyc_predictions, x = "hour", y = "Predicted_Load").set(title = "NYC Predicted Load by Hour")
plt.show()

sns.boxplot(nyc_predictions, x = "hour", y = "Actual_Load").set(title = "NYC Actual Load by Hour")
plt.show()

# +
avg_peak_month_load = nyc_predictions.query("month == 7")[["Predicted_Load", "Actual_Load"]].mean()
print(avg_peak_month_load)

avg_peak_hourly_load = nyc_predictions.query("hour == 22")[["Predicted_Load", "Actual_Load"]].mean()
print(avg_peak_hourly_load)
