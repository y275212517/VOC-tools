#! /usr/bin/env python
# -*- coding: utf-8 -*-
## @package mobilenet_trainer
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import logging
import numpy as np
import time
import os
import cv2
import shutil

from caffe2.python import core, workspace, experiment_util, data_parallel_model
from caffe2.python import dyndep, optimizer
from caffe2.python import timeout_guard, model_helper, brew, net_drawer
from caffe2.python import net_drawer

import mobilenet as mobilenet
import caffe2.python.predictor.predictor_exporter as pred_exp
import caffe2.python.predictor.predictor_py_utils as pred_utils
from caffe2.python.predictor_constants import predictor_constants as predictor_constants

'''
Parallelized multi-GPU distributed trainer for mobilenet. Can be used to train
on imagenet data, for example.

To run the trainer in single-machine multi-gpu mode by setting num_shards = 1.

To run the trainer in multi-machine multi-gpu mode with M machines,
run the same program on all machines, specifying num_shards = M, and
shard_id = a unique integer in the set [0, M-1].

For rendezvous (the trainer processes have to know about each other),
you can either use a directory path that is visible to all processes
(e.g. NFS directory), or use a Redis instance. Use the former by
passing the `file_store_path` argument. Use the latter by passing the
`redis_host` and `redis_port` arguments.
'''
logging.basicConfig()
log = logging.getLogger("mobilenet_trainer")
log.setLevel(logging.DEBUG)
dyndep.InitOpsLibrary('@/caffe2/caffe2/distributed:file_store_handler_ops')
dyndep.InitOpsLibrary('@/caffe2/caffe2/distributed:redis_store_handler_ops')

r_loss = [3]
r_train_accuracy = [0]
r_test_accuracy = [0]

current_folder = os.path.join(os.path.expanduser("~"), "data/VOCdevkit/dataDB")

if not os.path.exists(current_folder):
    print("%s is not exists." % current_folder)
    exit(0)
root_folder = os.path.join(current_folder, 'test_files')

if not os.path.exists(root_folder):
    os.makedirs(root_folder)

#=========== Set Flag: Flase ============
flag = False
np.save("flag.npy", flag)
#=========== Set Flag: Flase ============

store_folder = os.path.join(current_folder, 'store_files')
if not os.path.exists(store_folder):
    os.makedirs(store_folder)

finetune = None
finetune = os.path.join(store_folder, 'mobilenet_61.mdl')
#=================================================
if finetune is not None:
    loa = np.load("result.npz")
    # np.savez("result.npz", train = test_accuracy, test = test_accuracy)
    r_train_accuracy = list(loa["train"])
    r_test_accuracy = list(loa["test"])
    r_loss = list(loa["loss"])
#=================================================

def AddImageInput(model, reader, batch_size, img_size):
    '''
    Image input operator that loads data from reader and
    applies certain transformations to the images.
    '''
    data, label = brew.image_input(
        model,
        reader, ["data", "label"],
        batch_size=batch_size,
        use_caffe_datum=False,
        mean=128.,
        std=128.,
        minsize=img_size,
        crop=img_size,
        mirror=1
    )
    data = model.StopGradient(data, data)

def CheckSave():

    #============== Record Data===============
    np.savez("result.npz", train = r_train_accuracy , test = r_test_accuracy, loss = r_loss)
    #============== Record Data===============
    #============== Break Flag===============
    flag = np.load("flag.npy")
    if np.load("flag.npy"):
        exit(0)
    #============== Break Flag===============


def SaveModel(args, train_model, epoch):
    prefix = "gpu_{}".format(train_model._devices[0])
    predictor_export_meta = pred_exp.PredictorExportMeta(
        predict_net=train_model.net.Proto(),
        parameters=data_parallel_model.GetCheckpointParams(train_model),
        inputs=[prefix + "/data"],
        outputs=[prefix + "/softmax"],
        shapes={
            prefix + "/softmax": (1, args.num_labels),
            prefix + "/data": (args.num_channels, args.image_size, args.image_size)
        }
    )

    # save the train_model for the current epoch
    model_path = "%s/%s_%d.mdl" % (
        args.file_store_path,
        args.save_model_name,
        epoch,
    )

    # set db_type to be "minidb" instead of "log_file_db", which breaks
    # the serialization in save_to_db. Need to switch back to log_file_db
    # after migration
    pred_exp.save_to_db(
        db_type="minidb",
        db_destination=model_path,
        predictor_export_meta=predictor_export_meta,
    )


def LoadModel(path, model):
    '''
    Load pretrained model from file
    '''
    log.info("Loading path: {}".format(path))
    meta_net_def = pred_exp.load_from_db(path, 'minidb')
    init_net = core.Net(pred_utils.GetNet(
        meta_net_def, predictor_constants.GLOBAL_INIT_NET_TYPE))
    predict_init_net = core.Net(pred_utils.GetNet(
        meta_net_def, predictor_constants.PREDICT_INIT_NET_TYPE))

    predict_init_net.RunAllOnGPU()
    init_net.RunAllOnGPU()
    assert workspace.RunNetOnce(predict_init_net)
    assert workspace.RunNetOnce(init_net)


def RunEpoch(
        args,
        epoch,
        train_model,
        test_model,
        total_batch_size,
        num_shards,
        expname,
        explog,
):
    '''
    Run one epoch of the trainer.
    TODO: add checkpointing here.
    '''
    # TODO: add loading from checkpoint
    log.info("Starting epoch {}/{}".format(epoch, args.num_epochs))
    epoch_iters = int(args.epoch_size / total_batch_size / num_shards)
    train_accuracy = 0
    train_loss = 0
    display_count = 20
    prefix = "gpu_{}".format(train_model._devices[0])
    for i in range(epoch_iters):
        # This timeout is required (temporarily) since CUDA-NCCL
        # operators might deadlock when synchronizing between GPUs.
        timeout = 600.0 if i == 0 else 60.0
        with timeout_guard.CompleteInTimeOrDie(timeout):
            t1 = time.time()
            workspace.RunNet(train_model.net.Proto().name)
            t2 = time.time()
            dt = t2 - t1
        train_accuracy += workspace.FetchBlob(prefix + '/accuracy')
        train_loss += workspace.FetchBlob(prefix + '/loss')
        if (i + 1) % display_count == 0:# or (i + 1) % epoch_iters == 0:
            fmt = "Finished iteration {}/{} of epoch {} ({:.2f} images/sec)"
            log.info(fmt.format(i + 1, epoch_iters, epoch, total_batch_size / dt))
            train_fmt = "Training loss: {}, accuracy: {}"
            log.info(train_fmt.format(train_loss / display_count, train_accuracy / display_count))

            r_train_accuracy.append(train_accuracy / display_count)
            r_loss.append(train_loss / display_count)
            
            train_accuracy = 0
            train_loss = 0
            
            test_accuracy = 0
            ntests = 0
            for _ in range(0, 20):
                workspace.RunNet(test_model.net.Proto().name)
                for g in test_model._devices:
                    test_accuracy += np.asscalar(workspace.FetchBlob(
                        "gpu_{}".format(g) + '/accuracy'
                    ))
                    ntests += 1
            test_accuracy /= ntests
            r_test_accuracy.append(test_accuracy) #my
    # print(dir(data_parallel_model))

    # exit(0)
    num_images = epoch * epoch_iters * total_batch_size
    prefix = "gpu_{}".format(train_model._devices[0])
    accuracy = workspace.FetchBlob(prefix + '/accuracy')
    loss = workspace.FetchBlob(prefix + '/loss')
    learning_rate = workspace.FetchBlob('SgdOptimizer_0_lr_gpu0')
    test_accuracy = 0
    if (test_model is not None):
        # Run 100 iters of testing
        ntests = 0
        for _ in range(0, 100):
            workspace.RunNet(test_model.net.Proto().name)
            for g in test_model._devices:
                test_accuracy += np.asscalar(workspace.FetchBlob(
                    "gpu_{}".format(g) + '/accuracy'
                ))
                ntests += 1
        test_accuracy /= ntests
        r_test_accuracy.append(test_accuracy) #my
    else:
        test_accuracy = (-1)
    test_fmt = "Testing accuracy: {}"
    log.info(test_fmt.format(test_accuracy))

    explog.log(
        input_count=num_images,
        batch_count=(i + epoch * epoch_iters),
        additional_values={
            'accuracy': accuracy,
            'loss': loss,
            'learning_rate': learning_rate,
            'epoch': epoch,
            'test_accuracy': test_accuracy,
        }
    )
    assert loss < 40, "Exploded gradients :("

    # TODO: add checkpointing
    return epoch + 1


def Train(args):
    # Either use specified device list or generate one
    if args.gpus is not None:
        gpus = [int(x) for x in args.gpus.split(',')]
        num_gpus = len(gpus)
    else:
        gpus = list(range(args.num_gpus))
        num_gpus = args.num_gpus

    log.info("Running on GPUs: {}".format(gpus))

    # Verify valid batch size
    total_batch_size = args.batch_size
    batch_per_device = total_batch_size // num_gpus
    assert \
        total_batch_size % num_gpus == 0, \
        "Number of GPUs must divide batch size"

    # Round down epoch size to closest multiple of batch size across machines
    global_batch_size = total_batch_size * args.num_shards
    epoch_iters = int(args.epoch_size / global_batch_size)
    args.epoch_size = epoch_iters * global_batch_size
    log.info("Using epoch size: {}".format(args.epoch_size))

    # Create ModelHelper object
    train_arg_scope = {
        'order': 'NCHW',
        'use_cudnn': True,
        'cudnn_exhaustice_search': True,
        'ws_nbytes_limit': (args.cudnn_workspace_limit_mb * 1024 * 1024),
    }
    train_model = model_helper.ModelHelper(
        name="mobilenet", arg_scope=train_arg_scope
    )

    num_shards = args.num_shards
    shard_id = args.shard_id
    if num_shards > 1:
        '''
        # Create rendezvous for distributed computation
        store_handler = "store_handler"
        if args.redis_host is not None:
            # Use Redis for rendezvous if Redis host is specified
            workspace.RunOperatorOnce(
                core.CreateOperator(
                    "RedisStoreHandlerCreate", [], [store_handler],
                    host=args.redis_host,
                    port=args.redis_port,
                    prefix=args.run_id,
                )
            )
        else:
            # Use filesystem for rendezvous otherwise
            workspace.RunOperatorOnce(
                core.CreateOperator(
                    "FileStoreHandlerCreate", [], [store_handler],
                    path=args.file_store_path,
                )
            )
        rendezvous = dict(
            kv_handler=store_handler,
            shard_id=shard_id,
            num_shards=num_shards,
            engine="GLOO",
            exit_nets=None)
        '''
    else:
        rendezvous = None

    # Model building functions
    def create_mobilenet_model_ops(model, loss_scale):
        [softmax, loss] = mobilenet.create_mobilenet(
            model,
            "data",
            num_input_channels=args.num_channels,
            num_labels=args.num_labels,
            label="label"
        )
        loss = model.Scale(loss, scale=loss_scale)
        brew.accuracy(model, [softmax, "label"], "accuracy")
        return [loss]

    def add_optimizer(model):
        stepsz = int(200 * args.epoch_size / total_batch_size / num_shards)
        optimizer.add_weight_decay(model, args.weight_decay)
        optimizer.build_sgd(
            model,
            args.base_learning_rate,
            momentum=0.9,
            nesterov=1,
            policy="step",
            stepsize=stepsz,
            gamma=0.1
        )

    # Input. Note that the reader must be shared with all GPUS.
    reader = train_model.CreateDB(
        "reader",
        db=args.train_data,
        db_type=args.db_type,
        num_shards=num_shards,
        shard_id=shard_id,
    )

    def add_image_input(model):
        AddImageInput(
            model,
            reader,
            batch_size=batch_per_device,
            img_size=args.image_size,
        )
    def add_post_sync_ops(model):
        for param_info in model.GetOptimizationParamInfo(model.GetParams()):
            if param_info.blob_copy is not None:
                model.param_init_net.HalfToFloat(
                    param_info.blob,
                    param_info.blob_copy[core.DataType.FLOAT]
                )

    # Create parallelized model
    data_parallel_model.Parallelize_GPU(
        train_model,
        input_builder_fun=add_image_input,
        forward_pass_builder_fun=create_mobilenet_model_ops,
        optimizer_builder_fun=add_optimizer,
        post_sync_builder_fun=add_post_sync_ops,
        devices=gpus,
        rendezvous=rendezvous,
        optimize_gradient_memory=True,
    )
    #
    # # save network graph
    # graph = net_drawer.GetPydotGraphMinimal(
    #     train_model.net.Proto().op, "mobilenet", rankdir="LR", minimal_dependency=True)
    # with open("mobilenet.png", 'wb') as fid:
    #     fid.write(graph.create_png())

    # Add test model, if specified
    test_model = None
    if (args.test_data is not None):
        log.info("----- Create test net ----")
        test_arg_scope = {
            'order': "NCHW",
            'use_cudnn': True,
            'cudnn_exhaustive_search': True,
        }
        test_model = model_helper.ModelHelper(
            name="mobilenet_test", arg_scope=test_arg_scope
        )

        test_reader = test_model.CreateDB(
            "test_reader",
            db=args.test_data,
            db_type=args.db_type,
        )

        def test_input_fn(model):
            AddImageInput(
                model,
                test_reader,
                batch_size=batch_per_device,
                img_size=args.image_size,
            )

        data_parallel_model.Parallelize_GPU(
            test_model,
            input_builder_fun=test_input_fn,
            forward_pass_builder_fun=create_mobilenet_model_ops,
            post_sync_builder_fun=add_post_sync_ops,
            param_update_builder_fun=None,
            devices=gpus,
        )
        workspace.RunNetOnce(test_model.param_init_net)
        workspace.CreateNet(test_model.net)
    workspace.RunNetOnce(train_model.param_init_net)
    workspace.CreateNet(train_model.net)

    epoch = 0
    # load the pre-trained model and mobilenet epoch
    
    if args.load_model_path is not None:
        LoadModel(args.load_model_path, train_model)

        # Sync the model params

        data_parallel_model.FinalizeAfterCheckpoint(train_model)

        x = workspace.FetchBlob('optimizer_iteration')
        workspace.FeedBlob('optimizer_iteration', x)

        # mobilenet epoch. load_model_path should end with *_X.mdl,
        # where X is the epoch number
        last_str = args.load_model_path.split('_')[-1]
        if last_str.endswith('.mdl'):
            epoch = int(last_str[:-4])
            log.info("mobilenet epoch to {}".format(epoch))
        else:
            log.warning("The format of load_model_path doesn't match!")
    # else:
    #     workspace.RunNetOnce(train_model.param_init_net)
    #     workspace.CreateNet(train_model.net)

    expname = "mobilenet_gpu%d_b%d_L%d_lr%.2f_v2" % (
        args.num_gpus,
        total_batch_size,
        args.num_labels,
        args.base_learning_rate,
    )
    explog = experiment_util.ModelTrainerLog(expname, args)

    # Run the training one epoch a time
    # with open(os.path.join(root_folder, "train_net.pbtxt"), 'w') as fo:
    #     fo.write(str(train_model.net.Proto()))
    # with open(os.path.join(root_folder, "train_init_net.pbtxt"), 'w') as fo:
    #     fo.write(str(train_model.param_init_net.Proto()))
    # print(workspace.FetchBlob('iteration_mutex'))
    # print(workspace.FetchBlob('gpu_0/conv1_b'))

    while epoch < args.num_epochs:
        epoch = RunEpoch(
            args,
            epoch,
            train_model,
            test_model,
            total_batch_size,
            num_shards,
            expname,
            explog
        )

        # Save the model for each epoch
        SaveModel(args, train_model, epoch)
        
        model_path = "%s/%s_" % (
            args.file_store_path,
            args.save_model_name
        )
        # remove the saved model from the previous epoch if it exists

        if os.path.isfile(model_path + str(epoch - 3) + ".mdl"):
            os.remove(model_path + str(epoch - 3) + ".mdl")

#======= Check Flag ==========
        CheckSave()
#======= Check Flag ==========


def main():
    # TODO: use argv


    data_folder = os.path.join(current_folder)

    # root_folder = os.path.join(current_folder, 'test_files')

    # train_data_db = os.path.join(data_folder, "trainvlaDB_t200_lmdb")
    train_data_db = os.path.join(data_folder, "trainvlaDB_lmdb")
    train_data_db_type = "lmdb"
    train_data_count = 50728
    
    #train_data_count = 50728#50728
    #test_data_count = 12032#12032

    test_data_db = os.path.join(data_folder, "testDB_sub_lmdb")
    test_data_db_type = "lmdb"
    test_data_count = 12032


    parser = argparse.ArgumentParser(
        description="Caffe2: mobilenet training"
    )
    parser.add_argument("--train_data", type=str, default=train_data_db,
                        help="Path to training data or 'everstore_sampler'")
    parser.add_argument("--test_data", type=str, default=test_data_db,
                        help="Path to test data")
    parser.add_argument("--db_type", type=str, default="lmdb",
                        help="Database type (such as lmdb or minidb)")
    parser.add_argument("--gpus", type=str,
                        help="Comma separated list of GPU devices to use")
    parser.add_argument("--num_gpus", type=int, default=1,
                        help="Number of GPU devices (instead of --gpus)")
    parser.add_argument("--num_channels", type=int, default=3,
                        help="Number of color channels")
    parser.add_argument("--image_size", type=int, default=224,
                        help="Input image size (to crop to)")
    parser.add_argument("--num_labels", type=int, default=20,
                        help="Number of labels")
    parser.add_argument("--batch_size", type=int, default=32,
                        help="Batch size, total over all GPUs")
    parser.add_argument("--epoch_size", type=int, default=train_data_count,
                        help="Number of images/epoch, total over all machines")
    parser.add_argument("--num_epochs", type=int, default=200,
                        help="Num epochs.")
    parser.add_argument("--base_learning_rate", type=float, default=0.003,
                        help="Initial learning rate.")
    parser.add_argument("--weight_decay", type=float, default=1e-3,
                        help="Weight decay (L2 regularization)")
    parser.add_argument("--cudnn_workspace_limit_mb", type=int, default=64,
                        help="CuDNN workspace limit in MBs")
    parser.add_argument("--num_shards", type=int, default=1,
                        help="Number of machines in distributed run")
    parser.add_argument("--shard_id", type=int, default=0,
                        help="Shard id.")
    parser.add_argument("--run_id", type=str,
                        help="Unique run identifier (e.g. uuid)")
    parser.add_argument("--redis_host", type=str,
                        help="Host of Redis server (for rendezvous)")
    parser.add_argument("--redis_port", type=int, default=6379,
                        help="Port of Redis server (for rendezvous)")
    parser.add_argument("--file_store_path", type=str, default=store_folder,
                        help="Path to directory to use for rendezvous")
    parser.add_argument("--save_model_name", type=str, default="mobilenet",
                        help="Save the trained model to a given name")
    parser.add_argument("--load_model_path", type=str, default=finetune,
                        help="Load previously saved model to continue training")
    
    args = parser.parse_args()

    Train(args)


if __name__ == '__main__':
    workspace.GlobalInit(['caffe2', '--caffe2_log_level=2'])
    # from caffe2.python.utils import DebugMode
    # DebugMode.run(main())
    main()