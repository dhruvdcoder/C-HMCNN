import os
import importlib
os.environ["DATA_FOLDER"] = "./"
import wandb
import argparse

import torch
import torch.utils.data
import torch.nn as nn

from utils.parser import *
from utils import datasets
import random
import sys

from sklearn.impute import SimpleImputer

from sklearn import preprocessing
from sklearn.model_selection import train_test_split


from sklearn.metrics import f1_score, average_precision_score, precision_recall_curve, roc_auc_score, auc

def get_constr_out(x, R):
    """ Given the output of the neural network x returns the output of MCM given the hierarchy constraint expressed in the matrix R """
    c_out = x.double()
    c_out = c_out.unsqueeze(1)
    c_out = c_out.expand(len(x),R.shape[1], R.shape[1])
    R_batch = R.expand(len(x),R.shape[1], R.shape[1])
    final_out, _ = torch.max(R_batch*c_out.double(), dim = 2)
    return final_out


class ConstrainedFFNNModel(nn.Module):
    """ C-HMCNN(h) model - during training it returns the not-constrained output that is then passed to MCLoss """
    def __init__(self, input_dim, hidden_dim, output_dim, hyperparams, R):
        super(ConstrainedFFNNModel, self).__init__()
        
        self.nb_layers = hyperparams['num_layers']
        self.R = R
        
        fc = []
        for i in range(self.nb_layers):
            if i == 0:
                fc.append(nn.Linear(input_dim, hidden_dim))
            elif i == self.nb_layers-1:
                fc.append(nn.Linear(hidden_dim, output_dim))
            else:
                fc.append(nn.Linear(hidden_dim, hidden_dim))
        self.fc = nn.ModuleList(fc)
        
        self.drop = nn.Dropout(hyperparams['dropout'])
        
        
        self.sigmoid = nn.Sigmoid()
        if hyperparams['non_lin'] == 'tanh':
            self.f = nn.Tanh()
        else:
            self.f = nn.ReLU()
        
    def forward(self, x):
        for i in range(self.nb_layers):
            if i == self.nb_layers-1:
                x = self.sigmoid(self.fc[i](x))
            else:
                x = self.f(self.fc[i](x))
                x = self.drop(x)
        if self.training:
            constrained_out = x
        else:
            constrained_out = get_constr_out(x, self.R)
        return constrained_out

def main():
    wandb.init(project='multilabel-learning-datsets2', entity='iesl-boxes')
    # Training settings
    parser = argparse.ArgumentParser(description='Train neural network')
    
    # Required  parameters
    parser.add_argument('--dataset', type=str, required=True,
                        help='dataset')
    parser.add_argument('--batch_size', type=int, required=True,
                        help='input batch size for training')
    parser.add_argument('--lr', type=float, required=True,
                        help='learning rate')
    parser.add_argument('--dropout', type=float, required=True,
                        help='dropout probability')
    parser.add_argument('--hidden_dim', type=int, required=True,
                        help='size of the hidden layers')
    parser.add_argument('--num_layers', type=int, required=True,
                        help='number of hidden layers')
    parser.add_argument('--weight_decay', type=float, required=True,
                        help='weight decay')
    parser.add_argument('--non_lin', type=str, required=True,
                        help='non linearity function to be used in the hidden layers')

    # Other parameters 
    parser.add_argument('--device', type=int, default=0,
                        help='device (default:0)')
    parser.add_argument('--num_epochs', type=int, default=2000,
                        help='Max number of epochs to train (default:2000)')
    parser.add_argument('--seed', type=int, default=0,
                        help='random seed (default:0)')

    args = parser.parse_args()
    hyperparams = {'batch_size':args.batch_size, 'num_layers':args.num_layers, 'dropout':args.dropout, 'non_lin':args.non_lin, 'hidden_dim':args.hidden_dim, 'lr':args.lr, 'weight_decay':args.weight_decay}
    wandb.log(hyperparams)
    assert('_' in args.dataset)
    assert('FUN' in args.dataset or 'GO' in args.dataset or 'others' in args.dataset)

    
    # Load train, val and test set
    dataset_name = args.dataset
    data = dataset_name.split('_')[0]
    ontology = dataset_name.split('_')[1]


   # Dictionaries with number of features and number of labels for each dataset
    input_dims = {'diatoms':371, 'enron':1001,'imclef07a': 80, 'imclef07d': 80,'cellcycle':77, 'church':27, 'derisi':63, 'eisen':79, 'expr':561, 'gasch1':173, 'gasch2':52, 'hom':47034, 'seq':529, 'spo':86}
    output_dims_FUN = {'cellcycle':499, 'church':499, 'derisi':499, 'eisen':461, 'expr':499, 'gasch1':499, 'gasch2':499, 'hom':499, 'seq':499, 'spo':499}
    output_dims_GO = {'cellcycle':4122, 'church':4122, 'derisi':4116, 'eisen':3570, 'expr':4128, 'gasch1':4122, 'gasch2':4128, 'hom':4128, 'seq':4130, 'spo':4116}
    output_dims_others = {'diatoms':398,'enron':56, 'imclef07a': 96, 'imclef07d': 46, 'reuters':102}
    output_dims = {'FUN':output_dims_FUN, 'GO':output_dims_GO, 'others':output_dims_others}


    # Set seed
    seed = args.seed
    torch.manual_seed(seed)
    np.random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    random.seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    # Pick device
    device = torch.device("cuda:"+str(args.device) if torch.cuda.is_available() else "cpu")

    # Load the datasets
    if ('others' in args.dataset):
        train, test = initialize_other_dataset(dataset_name, datasets)
        train.to_eval, test.to_eval = torch.tensor(train.to_eval, dtype=torch.bool),  torch.tensor(test.to_eval, dtype=torch.bool)
        train.X, valX, train.Y, valY = train_test_split(train.X, train.Y, test_size=0.30, random_state=seed)
    else:
        train, val, test = initialize_dataset(dataset_name, datasets)
        train.to_eval, val.to_eval, test.to_eval = torch.tensor(train.to_eval, dtype=torch.bool), torch.tensor(val.to_eval, dtype=torch.bool), torch.tensor(test.to_eval, dtype=torch.bool)
    
    different_from_0 = torch.tensor(np.array((test.Y.sum(0)!=0), dtype = np.uint8), dtype=torch.uint8)

    # Compute matrix of ancestors R
    # Given n classes, R is an (n x n) matrix where R_ij = 1 if class i is ancestor of class j
    R = np.zeros(train.A.shape)
    np.fill_diagonal(R, 1)
    g = nx.DiGraph(train.A)
    for i in range(len(train.A)):
        descendants = list(nx.descendants(g, i))
        if descendants:
            R[i, descendants] = 1
    R = torch.tensor(R)
    #Transpose to get the ancestors for each node 
    R = R.transpose(1, 0)
    R = R.unsqueeze(0).to(device)


    # Rescale dataset and impute missing data
    if ('others' in args.dataset):
        scaler = preprocessing.StandardScaler().fit((train.X))
        imp_mean = SimpleImputer(missing_values=np.nan, strategy='mean').fit((train.X))
        train.X, train.Y = torch.tensor(scaler.transform(imp_mean.transform(train.X))).to(device), torch.tensor(train.Y).to(device)
        valX, valY = torch.tensor(scaler.transform(imp_mean.transform(valX))).to(device), torch.tensor(valY).to(device)
    else:
        scaler = preprocessing.StandardScaler().fit(np.concatenate((train.X, val.X)))
        imp_mean = SimpleImputer(missing_values=np.nan, strategy='mean').fit(np.concatenate((train.X, val.X)))
        val.X, val.Y = torch.tensor(scaler.transform(imp_mean.transform(val.X))).to(device), torch.tensor(val.Y).to(device)
        train.X, train.Y = torch.tensor(scaler.transform(imp_mean.transform(train.X))).to(device), torch.tensor(train.Y).to(device)        
    test.X, test.Y = torch.tensor(scaler.transform(imp_mean.transform(test.X))).to(device), torch.tensor(test.Y).to(device)
    test_dataset = [(x, y) for (x, y) in zip(test.X, test.Y)]
    test_loader = torch.utils.data.DataLoader(dataset=test_dataset, 
                                            batch_size=args.batch_size, 
                                            shuffle=False)
    # Create loaders 
    train_dataset = [(x, y) for (x, y) in zip(train.X, train.Y)]
    if ('others' in args.dataset):
        val_dataset = [(x, y) for (x, y) in zip(valX, valY)]
    else:
        val_dataset = [(x, y) for (x, y) in zip(val.X, val.Y)]

    train_loader = torch.utils.data.DataLoader(dataset=train_dataset, 
                                            batch_size=args.batch_size, 
                                            shuffle=True)

    val_loader = torch.utils.data.DataLoader(dataset=val_dataset, 
                                            batch_size=args.batch_size, 
                                            shuffle=False)


    num_epochs = args.num_epochs
    if 'GO' in dataset_name: 
        num_to_skip = 4
    else:
        num_to_skip = 1 

    # Create the model
    model = ConstrainedFFNNModel(input_dims[data], args.hidden_dim, output_dims[ontology][data]+num_to_skip, hyperparams, R)
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay) 
    criterion = nn.BCELoss()

    # Set patience 
    patience, max_patience = 7, 10
    max_score = 0.0

    # Create folder for the dataset (if it does not exist)
    if not os.path.exists('logs/'+str(dataset_name)+'/'):
         os.makedirs('logs/'+str(dataset_name)+'/')
    best_validation_micro_map = 0
    best_validation_samples_map = 0
    best_test_micro_map = 0
    best_test_samples_map = 0
    for epoch in range(num_epochs):
        total_train = 0.0
        correct_train = 0.0
        model.train()

        train_micro_map = 0

        for i, (x, labels) in enumerate(train_loader):

            x = x.to(device)
            labels = labels.to(device)

            # Clear gradients w.r.t. parameters
            optimizer.zero_grad()
            output = model(x.float())
        
            constr_output = get_constr_out(output, R)
            train_output = labels*output.double()
            train_output = get_constr_out(train_output, R)
            train_output = (1-labels)*constr_output.double() + labels*train_output
            loss = criterion(train_output[:,train.to_eval], labels[:,train.to_eval]) 
            predicted = constr_output.data > 0.5
            # Total number of labels
            total_train += labels.size(0) * labels.size(1)
            # Total correct predictions
            correct_train += (predicted == labels.byte()).sum()
            wandb.log({"epoch": epoch, "loss": loss.item()})          
            # Getting gradients w.r.t. parameters
            loss.backward()
            # Updating parameters
            optimizer.step()
            
        model.eval()
        constr_output = constr_output.to('cpu')
        labels = labels.to('cpu')
        train_micro_map = average_precision_score(labels[:,train.to_eval], constr_output.data[:,train.to_eval], average='micro') 
        train_samples_map = average_precision_score(labels[:,train.to_eval], constr_output.data[:,train.to_eval], average='samples')

        for i, (x,y) in enumerate(val_loader):
            x = x.to(device)
            y = y.to(device)

            constrained_output = model(x.float())
            predicted = constrained_output.data > 0.5
            # Total number of labels
            total = y.size(0) * y.size(1)
            # Total correct predictions
            correct = (predicted == y.byte()).sum()

            #Move output and label back to cpu to be processed by sklearn
            cpu_constrained_output = constrained_output.to('cpu')
            y = y.to('cpu')

            if i == 0:
                constr_val = cpu_constrained_output
                y_val = y
            else:
                constr_val = torch.cat((constr_val, cpu_constrained_output), dim=0)
                y_val = torch.cat((y_val, y), dim =0)

        val_micro_map = average_precision_score(y_val[:,train.to_eval], constr_val.data[:,train.to_eval], average='micro') 
        val_samples_map = average_precision_score(y_val[:,train.to_eval], constr_val.data[:,train.to_eval], average='samples')
        
        for i, (x,y) in enumerate(test_loader):

            model.eval()
                
            x = x.to(device)
            y = y.to(device)

            constrained_output = model(x.float())
            predicted = constrained_output.data > 0.5
            # Total number of labels
            total = y.size(0) * y.size(1)
            # Total correct predictions
            correct = (predicted == y.byte()).sum()

            #Move output and label back to cpu to be processed by sklearn
            predicted = predicted.to('cpu')
            cpu_constrained_output = constrained_output.to('cpu')
            y = y.to('cpu')

            if i == 0:
                predicted_test = predicted
                constr_test = cpu_constrained_output
                y_test = y
            else:
                predicted_test = torch.cat((predicted_test, predicted), dim=0)
                constr_test = torch.cat((constr_test, cpu_constrained_output), dim=0)
                y_test = torch.cat((y_test, y), dim =0)


        test_micro_map = average_precision_score(y_test[:,test.to_eval], constr_test.data[:,test.to_eval], average='micro')
        test_samples_map = average_precision_score(y_test[:,test.to_eval], constr_test.data[:,test.to_eval], average='samples')
        if val_micro_map > best_validation_micro_map:
           best_validation_micro_map = val_micro_map
           best_validation_samples_map = val_samples_map
           best_test_micro_map = test_micro_map
           best_test_samples_map = test_samples_map
           patience = max_patience
        else:
           patience = patience - 1

        wandb.log({"epoch": epoch, "best_validation_micro_map": best_validation_micro_map, "best_validation_samples_map": best_validation_samples_map, "samples_map": val_samples_map, "micro_map": val_micro_map, "accuracy_train":float(correct_train)/float(total_train), "accuracy": float(correct)/float(total), 'test_micro_map': test_micro_map,'test_samples_map': test_samples_map, 'best_test_micro_map': best_test_micro_map,'best_test_samples_map': best_test_samples_map})
        floss= open('logs/'+str(dataset_name)+'/measures_batch_size_'+str(args.batch_size)+'_lr_'+str(args.lr)+'_weight_decay_'+str(args.weight_decay)+'_seed_'+str(args.seed)+'_num_layers_'+str(args.num_layers)+'._hidden_dim_'+str(args.hidden_dim)+'_dropout_'+str(args.dropout)+'_'+args.non_lin, 'a')
        floss.write('\nEpoch: {} - Loss: {:.4f}, Accuracy train: {:.5f}, Accuracy: {:.5f}, micro_map: ({:.5f})\n'.format(epoch,
                    loss, float(correct_train)/float(total_train), float(correct)/float(total), val_micro_map))
        floss.close()

        if patience == 0:
            break

if __name__ == "__main__":
    main()
