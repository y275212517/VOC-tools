#! /usr/bin/env python
# -*- coding: utf-8 -*-

# =========== M U S T ===============

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

# =========== M U S T ===============

import os
import lmdb
import cv2
import numpy as np
from caffe2.proto import caffe2_pb2
from caffe2.python import workspace, model_helper

Image_width = 227
Image_height = 227
batch_size = 50

def display(env):
	txn = env.begin()
	cur = txn.cursor()
	for k, v in cur:
		print(k, len(v))

def create_data_db(dbpath, img_path, lab):
	db_env = lmdb.open(dbpath, map_size=int(1024*1024*1024*30)) # size:30GB
	# print(db_env.stat())
	# print(dir(db_env.info()))
	# with db_env.begin(write=True) as txn:
	txn = db_env.begin(write=True)
	tensor_protos = caffe2_pb2.TensorProtos()

	# print(type(tensor_protos))
	# print(dir(tensor_protos))
	for k, value in enumerate(img_path):
		# print("env1")
		# display(db_env)
		# print("env2")
		# print(k)
		lab_v = lab[k]
		# print(k, value, lab_v)
		img = cv2.imread(value, cv2.IMREAD_COLOR).astype(np.float32) / 255

		# print(img[0:2, 0:2].shape)

		# cv2.namedWindow('img', cv2.WINDOW_AUTOSIZE)
		# cv2.imshow('img', img)
		# cv2.waitKey(0)
		# cv2.destroyAllWindows()

		img_v = cv2.resize(img, (Image_height, Image_width), interpolation = cv2.INTER_AREA)
		img_v = img_v.swapaxes(1, 2).swapaxes(0, 1)
		# print(img_v.shape)
		img_tensor = tensor_protos.protos.add()
		img_tensor.dims.extend(img_v.shape)
		img_tensor.data_type = 1

		flatten_img = img_v.reshape(np.prod(img_v.shape))
		# img_tensor.float_data.extend(flatten_img)
		img_tensor.float_data.extend(flatten_img)

		lab_tensor = tensor_protos.protos.add()
		lab_tensor.data_type = 2
		lab_tensor.int32_data.append(lab_v)

		# txn.put(
		# 	'{}'.format(k).encode('ascii'),
		# 	tensor_protos.SerializeToString()
		# )
		
		# print(img_tensor)
		# print(lab_tensor)
		# # tensor_protos.Clear()
		# print("------")
		# print(tensor_protos)
		# print("----======--")
		txn.put(
			'{}'.format(k).encode('ascii'),
			tensor_protos.SerializeToString()
		)
		tensor_protos.Clear()
		if (batch_size - 1) == (k % batch_size):
			txn.commit()
			print("Commit for", k)
			txn = db_env.begin(write=True)

	txn.commit()
	db_env.close()

def read_data_db(dbpath):
	# db_env = lmdb.open(dbpath)#, map_size=int(1024*1024*1024*30)) # size:30GB
	# # print(dir(db_env.info()))
	# # with db_env.begin(write=True) as txn:
	# txn = db_env.begin()
	# display(db_env)
	# exit()
	model = model_helper.ModelHelper(name="lmdbtest")
	data, label = model.TensorProtosDBInput(
		[], ["data", "label"], batch_size = 10,
		db=dbpath, db_type="lmdb")
	workspace.RunNetOnce(model.param_init_net)
	workspace.CreateNet(model.net)
	for _ in range(0, 2):
		print("start")
		workspace.RunNet(model.net.Proto().name)
		print("stop")
		img_datas = workspace.FetchBlob("data")
		labels = workspace.FetchBlob("label")
		print(img_datas.shape)
		print(labels.shape)


def main():
	# print(lmdb.version())
	label_paths = os.path.expanduser("~/data/VOCdevkit/trainval/label/label_count_1.txt")
	db_path = os.path.expanduser("~/data/VOCdevkit/dataDB/trainvlaDB_lmdb")
	# db_path = os.path.expanduser("~/data/VOCdevkit/dataDB/trainvlaDB_t200_lmdb")
	if True == os.path.exists(db_path):
		print(db_path + "IS EXISTS")
		# exit()
	image_paths = []
	labels = []
	with open(label_paths, 'r') as fi:
		lsof = fi.readlines()
		# print(len(lsof))
		for lof in lsof:
			image_paths.append(lof.split()[0])
			labels.append(lof.split()[1].find("1"))
			# labels.append(lof.split()[1])#.count("1"))
		fi.close()

	# print(image_paths[0:3])
	# print(labels[0:3])
	
	create_data_db(db_path, image_paths, labels)
	# create_data_db(db_path, image_paths[:200], labels[:200])
	read_data_db(db_path)
	# read_data_db("/home/yroot/data")


if __name__ == '__main__':
	main()
'''
	mew_image_path = os.path.join(root_dir, dataset , "image")
	mew_label_path = os.path.join(root_dir, dataset , "label")
	
	if True != os.path.exists(mew_image_path):
		os.makedirs(mew_image_path)

	if True != os.path.exists(mew_label_path):
		os.makedirs(mew_label_path)


	new_labels = []
	image_paths = []
	label_paths = []
	dst_file = os.path.join(script_dir, dataset + ".txt")
	print(dst_file)
	with open(dst_file, 'r') as fi:
		lsof = fi.readlines()
		print(len(lsof))
		count = 0
		for lof in lsof:
			image_paths.append(os.path.join(root_dir, lof.split(" ")[0]))
			label_paths.append(os.path.join(root_dir, lof.split(" ")[1])[:-1])
		fi.close()

	for k, label_path in enumerate(label_paths):
		image_path = image_paths[k]
		print(image_path, label_path)
		img = cv2.imread(image_path, cv2.IMREAD_COLOR)#.astype(np.float32)

		# pyplot.figure()
		# pyplot.imshow(img)

		# cv2.namedWindow('img', cv2.WINDOW_AUTOSIZE)
		# cv2.imshow('img', img)
# 		cv2.waitKey(0)
# 		cv2.destroyAllWindows()

		x_label = parseXml(label_path)

		m_label = s_label[:]
		
		for k, value in enumerate(x_label):
			# print(k, value, value[0], value[1])
			if (value[0] == 'name'):
				# print(x_label[k + 2][0], x_label[k + 2][1], x_label[k + 1][0] ,x_label[k + 1][1])

				tmp_flag = NameDict[value[1]]
				tmp_label = s_label[:]
				m_label = setLabel(m_label, tmp_flag)
				tmp_label = setLabel(tmp_label, tmp_flag)
				
				# pyplot.figure(k)
				# pyplot.imshow(img[x_label[k + 2][0]:x_label[k + 2][1], x_label[k + 1][0]:x_label[k + 2][1]])
				win_name = value[1] + str(k)

				# cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE)
				# cv2.imshow(win_name, img[x_label[k + 2][0]:x_label[k + 2][1], x_label[k + 1][0]:x_label[k + 2][1]])
				
				sub_img = img[x_label[k + 2][0]:x_label[k + 2][1], x_label[k + 1][0]:x_label[k + 1][1]]

				tmp_file = os.path.join(root_dir, dataset , "image", x_label[0][1][:-4] + win_name + ".jpg")
				# print(os.path.exists(tmp_file))
				if True != os.path.exists(tmp_file):
					cv2.imwrite(tmp_file, sub_img, [int(cv2.IMWRITE_JPEG_QUALITY), 10])

				# tmp_label = os.path.join(root_dir, dataset , "label", x_label[0][1][:-4] + win_name + ".jpg")
				new_labels.append((tmp_file, tmp_label))

				#sub_img = sub_img.astype(np.float32) - mean
				#sub_img = cv2.resize(sub_img, (INPUT_IMAGE_SIZE, INPUT_IMAGE_SIZE), interpolation = cv2.INTER_AREA)

				# cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE)
				# cv2.imshow(win_name, sub_img)

				# sub_img = sub_img.swapaxes(1, 2).swapaxes(0, 1)
				# sub_img = sub_img[np.newaxis, :, :, :].astype(np.float32)

				# win_name1 = value[1] + str(k) + "1"
				# cv2.namedWindow(win_name1, cv2.WINDOW_AUTOSIZE)
				# cv2.imshow(win_name1, img)
				# cv2.waitKey(0)
				# cv2.destroyAllWindows()

				# print(img[x_label[k + 2][0]:x_label[k + 2][1], x_label[k + 1][0]:x_label[k + 2][1]].shape)

		# new_labels.append((image_path, m_label)) Just single label classsfier

	# print(new_labels[:])

	with open(os.path.join(root_dir, dataset, "label", "label.txt"), 'w') as fo:
		for k, label in enumerate(new_labels):
			print(str(label))
			fo.write(str(label[0]) + " " + str(label[1])+ "\n")
		fo.close()

	# with open(os.path.join(root_dir, dataset, "label", "label.txt"), 'r') as fi:
	# 	lsof = fi.readlines()
	# 	print(len(lsof[0].split()[1]))
	# 	fi.close()
'''