#!/usr/bin/env python

import logging
import gc
import os
import joblib
import pathlib
import numpy as np
import argparse
import trainUtils, utils
import cooler

def main(args):
    
    # Configure logging
    logging.basicConfig(filename='peakachu_train_models.log', level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info('Starting training process')
    np.seterr(divide='ignore', invalid='ignore')

    pathlib.Path(args.output).mkdir(parents=True, exist_ok=True)
    logging.info('Output directory verified/created')
    
    # decide which normalization method to use
    if args.clr_weight_name.lower() == 'raw':
        correct = False
    else:
        correct = args.clr_weight_name

    # more robust to check if a file is .hic
    hic_info = utils.read_hic_header(args.path)

    if hic_info is None:
        hic = False
    else:
        hic = True

    #get res of input
    res = args.resolution
    #loop anchors of positive training set
    coords = trainUtils.parsebed(args.bedpe, lower=(args.width+1)*res) 
    #getting the 1-d genomic distribution of the positive loop anchors 
    kde, lower, long_start, long_end = trainUtils.learn_distri_kde(coords, res=res)

    if not hic:
        Lib = cooler.Cooler(args.path)
        chromosomes = Lib.chromnames[:]
    else:
        chromosomes = utils.get_hic_chromosomes(args.path, res)

    # train model per chromosome
    #extract intra-chr matrices and save normalised ones in X
    collect = {} #dictionary to store training samples for each chromosome
    for key in chromosomes:
        if key.startswith('chr'):
            chromname = key
        else:
            chromname = 'chr'+key
        print('collecting from {}'.format(key))
        if not hic:
            tmp = Lib.matrix(balance=correct, sparse=True).fetch(key)
            X = utils.tocsr(tmp)
        else:
            if correct:
                X = utils.csr_contact_matrix(
                    'KR', args.path, key, key, 'BP', res)
            else:
                X = utils.csr_contact_matrix(
                    'NONE', args.path, key, key, 'BP', res)
        # log X.shape
        print(f"Matrix shape: {X.shape}")
        
        # deal with the situation when resolutions of the matrix
        # and the training set are different
        clist = []
        for s1, e1, s2, e2 in coords[chromname]: #iterating over loop anchors from the positive training set
            bins1 = range(s1//res, (e1+res-1)//res)
            bins2 = range(s2//res, (e2+res-1)//res)
            maxv = 0
            binpair = None
            for b1 in bins1:
                for b2 in bins2:
                    if b1 < X.shape[0] and b2 < X.shape[1]:
                        if X[b1, b2] > maxv: #if in the input (X) the +ve sample is actually seen in terms of contact strength
                            maxv = X[b1, b2]
                            binpair = (b1, b2)
                    else:
                        print(f"Skipping out-of-range indices: {b1}, {b2}")
            if maxv > 0:
                clist.append(binpair)

        try:
            neg_coords = trainUtils.negative_generating(
                X, kde, clist, lower, long_start, long_end)
            #generate samples (11x11 bin-window of 10kb) around the positive binpairs
            pos_set = trainUtils.buildmatrix(X, clist, w=args.width) 
            neg_set = trainUtils.buildmatrix(X, neg_coords, w=args.width)
            if (not pos_set is None) and (not neg_set is None):
                neg_set = neg_set[:len(pos_set)] #class imbalance correction
                trainset = pos_set + neg_set
                trainset = np.r_[trainset] #creating a single numpy array for training
                labels = [1] * len(pos_set) + [0] * len(neg_set)
                labels = np.r_[labels]
                collect[chromname] = [trainset, labels] #dictionary to store training samples for each chromosome
            else:
                print(chromname, ' failed to gather fts')
        except:
            print(chromname, ' failed to gather fts')

    for key in chromosomes:
        if key.startswith('chr'):
            chromname = key
        else:
            chromname = 'chr'+key
            
        trainset = []
        labels_ = np.r_[[]]
        for ci in collect:
            if (ci != chromname) and (len(collect[ci][1]) > 1):
                trainset.append(collect[ci][0])
                labels_ = np.r_[labels_, collect[ci][1]]
        trainset = np.vstack(trainset)
        pn = np.count_nonzero(labels_)
        nn = labels_.size - pn
        print(chromname, 'pos/neg: ', pn, nn)

        #train model
        model = trainUtils.trainRF(trainset, labels_, nproc=args.nproc)
        checkpoint_dir = args.output
        model_filename = os.path.join(checkpoint_dir, f'{chromname}.pkl')
        logging.info(f'Saving model for {chromname}')
        
        try:
            joblib.dump(model, os.path.join(args.output, '{0}.pkl'.format(chromname)), compress=('xz', 3))
            logging.info(f'Model saved for {chromname}')
        except Exception as e:
            logging.error(f'Failed to save model for {chromname}: {e}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="training xgboost for calling loops")
    #arguments to the parser 
    parser.add_argument('--resolution', type=int, default=10000, help='Resolution of input HiC')
    parser.add_argument('--bedpe', type=str, help='BEDPE loop-anchors from orthogonal data (+ve set)')
    parser.add_argument('--path', type=str, help='Path to .cool input')
    parser.add_argument('--output', type=str, help='Output directory for saving models')
    parser.add_argument('--clr_weight_name', type=str, default='raw', help='Normalization method. Default is "raw"')
    parser.add_argument('--width', type=int, default=5, help='Width description')
    parser.add_argument('--nproc', type=int, default=4, help='Number of processors')

    # Parse arguments from command line
    args = parser.parse_args()

    # Call main function with parsed arguments
    main(args)
