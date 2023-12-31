import os
import numpy as np, argparse, time, pickle, random
import torch
import torch.nn as nn
import torch_optimizer as optim
from model import *
from sklearn.metrics import f1_score, confusion_matrix, accuracy_score, classification_report, \
    precision_recall_fscore_support
from trainer import  train_or_eval_model, save_badcase
from dataset import MELDDataset
from dataloader import get_MELD_loaders
import adabound

# We use seed = 100 for reproduction of the results reported in the paper. We use "0"th GPU for training
seed = 100
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

import logging

def get_logger(filename, verbosity=1, name=None):
    level_dict = {0: logging.DEBUG, 1: logging.INFO, 2: logging.WARNING}
    formatter = logging.Formatter(
        "[%(asctime)s][%(filename)s][line:%(lineno)d][%(levelname)s] %(message)s"
    )
    logger = logging.getLogger(name)
    logger.setLevel(level_dict[verbosity])

    fh = logging.FileHandler(filename, "w")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger

def seed_everything(seed=seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


if __name__ == '__main__':

    path = './saved_models/'

    parser = argparse.ArgumentParser()
    parser.add_argument('--bert_model_dir', type=str, default='')
    parser.add_argument('--bert_tokenizer_dir', type=str, default='')


    parser.add_argument('--bert_dim', type = int, default=1024)
    parser.add_argument('--hidden_dim', type = int, default=300)
    parser.add_argument('--mlp_layers', type=int, default=2, help='Number of output mlp layers.')
    parser.add_argument('--gnn_layers', type=int, default=2, help='Number of gnn layers.')
    parser.add_argument('--emb_dim', type=int, default=1024, help='Feature size.')


    parser.add_argument('--max_sent_len', type=int, default=200,
                        help='max content length for each text, if set to 0, then the max length has no constrain')

    parser.add_argument('--no_cuda', action='store_true', default=False, help='does not use GPU')

    parser.add_argument('--dataset_name', default='EmoryNLP', type= str, help='dataset name, IEMOCAP or MELD or DailyDialog or EmoryNLP')


    parser.add_argument('--max_grad_norm', type=float, default=5.0, help='Gradient clipping.')

    parser.add_argument('--lr', type=float, default=5e-3, metavar='LR', help='learning rate')

    parser.add_argument('--dropout', type=float, default=0.2, metavar='dropout', help='dropout rate')

    parser.add_argument('--batch_size', type=int, default=16, metavar='BS', help='batch size')

    parser.add_argument('--epochs', type=int, default=30, metavar='E', help='number of epochs')

    parser.add_argument('--tensorboard', action='store_true', default=False, help='Enables tensorboard log')


    args = parser.parse_args()
    print(args)

    seed_everything(42)

    args.cuda = torch.cuda.is_available() and not args.no_cuda

    if args.cuda:
        print('Running on GPU')
    else:
        print('Running on CPU')

    if args.tensorboard:
        from tensorboardX import SummaryWriter

        writer = SummaryWriter()

    logger = get_logger(path + args.dataset_name + '/logging.log')
    logger.info('start training on GPU {}!'.format(os.environ["CUDA_VISIBLE_DEVICES"]))
    logger.info(args)

    cuda = args.cuda
    n_epochs = args.epochs
    batch_size = args.batch_size
    print("======================LOADING DATA=================")
    train_loader, valid_loader, test_loader, speaker_vocab, label_vocab, person_vec = get_MELD_loaders(dataset_name=args.dataset_name, batch_size=batch_size, num_workers=0, args = args)
    n_classes = len(label_vocab['itos'])
    print("====================LOADING OVER ===========")

    print('building model..')
    model = Model_DSTM(args, n_classes)
    # model = DAGERC_fushion(args,n_classes)


    if torch.cuda.device_count() > 1:
        print('Multi-GPU...........')
        model = nn.DataParallel(model,device_ids = range(torch.cuda.device_count()))
    if cuda:
        model.cuda()

    loss_function = nn.CrossEntropyLoss(ignore_index=-1)
    # optimizer = optim.AdaBound(
    #                             model.parameters(),
    #                             lr= args.lr,
    #                             betas=(0.9, 0.999),
    #                             final_lr = 0.1,
    #                             eps=1e-8,
    #                             weight_decay=0,
    #                         )
    optimizer = adabound.AdaBound(model.parameters(),lr = 1e-4)
    best_fscore,best_acc, best_loss, best_label, best_pred, best_mask = None,None, None, None, None, None
    all_fscore, all_acc, all_loss = [], [], []
    best_acc = 0.
    best_fscore = 0.

    best_model = None
    for e in range(n_epochs):
        start_time = time.time()

        if args.dataset_name=='DailyDialog':
            train_loss, train_acc, _, _, train_micro_fscore, train_macro_fscore = train_or_eval_model(model, loss_function,
                                                                                                train_loader, e, cuda,
                                                                                                args, optimizer, True)
            valid_loss, valid_acc, _, _, valid_micro_fscore, valid_macro_fscore = train_or_eval_model(model, loss_function,
                                                                                                valid_loader, e, cuda, args)
            test_loss, test_acc, test_label, test_pred, test_micro_fscore, test_macro_fscore = train_or_eval_model(model,loss_function, test_loader, e, cuda, args)

            all_fscore.append([valid_micro_fscore, test_micro_fscore, valid_macro_fscore, test_macro_fscore])

            logger.info( 'Epoch: {}, train_loss: {}, train_acc: {}, train_micro_fscore: {}, train_macro_fscore: {}, valid_loss: {}, valid_acc: {}, valid_micro_fscore: {}, valid_macro_fscore: {}, test_loss: {}, test_acc: {}, test_micro_fscore: {}, test_macro_fscore: {}, time: {} sec'. \
                    format(e + 1, train_loss, train_acc, train_micro_fscore, train_macro_fscore, valid_loss, valid_acc, valid_micro_fscore, valid_macro_fscore, test_loss, test_acc,
                        test_micro_fscore, test_macro_fscore, round(time.time() - start_time, 2)))

        else:
            train_loss, train_acc, _, _, train_fscore = train_or_eval_model(model, loss_function,
                                                                            train_loader, e, cuda,
                                                                            args, optimizer, True)
            valid_loss, valid_acc, _, _, valid_fscore= train_or_eval_model(model, loss_function,
                                                                            valid_loader, e, cuda, args)
            test_loss, test_acc, test_label, test_pred, test_fscore= train_or_eval_model(model,loss_function, test_loader, e, cuda, args)

            all_fscore.append([valid_fscore, test_fscore])

            logger.info( 'Epoch: {}, train_loss: {}, train_acc: {}, train_fscore: {}, valid_loss: {}, valid_acc: {}, valid_fscore: {}, test_loss: {}, test_acc: {}, test_fscore: {}, time: {} sec'. \
                format(e + 1, train_loss, train_acc, train_fscore, valid_loss, valid_acc, valid_fscore, test_loss, test_acc,
                test_fscore, round(time.time() - start_time, 2)))

        #torch.save(model.state_dict(), path + args.dataset_name + '/model_' + str(e) + '_' + str(test_acc)+ '.pkl')

        e += 1


    if args.tensorboard:
        writer.close()

    logger.info('finish training!')

    #print('Test performance..')
    all_fscore = sorted(all_fscore, key=lambda x: (x[0],x[1]), reverse=True)
    #print('Best F-Score based on validation:', all_fscore[0][1])
    #print('Best F-Score based on test:', max([f[1] for f in all_fscore]))

    #logger.info('Test performance..')
    #logger.info('Best F-Score based on validation:{}'.format(all_fscore[0][1]))
    #logger.info('Best F-Score based on test:{}'.format(max([f[1] for f in all_fscore])))

    if args.dataset_name=='DailyDialog':
        logger.info('Best micro/macro F-Score based on validation:{}/{}'.format(all_fscore[0][1],all_fscore[0][3]))
        all_fscore = sorted(all_fscore, key=lambda x: x[1], reverse=True)
        logger.info('Best micro/macro F-Score based on test:{}/{}'.format(all_fscore[0][1],all_fscore[0][3]))
    else:
        logger.info('Best F-Score based on validation:{}'.format(all_fscore[0][1]))
        logger.info('Best F-Score based on test:{}'.format(max([f[1] for f in all_fscore])))

    #save_badcase(best_model, test_loader, cuda, args, speaker_vocab, label_vocab)
