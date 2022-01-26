from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys

import numpy as np
import tensorflow as tf


# The operation used to print out the configuration
def print_configuration_op(FLAGS):
    print('[Configurations]:')
    # pdb.set_trace()
    for name in FLAGS.__flags.keys():
        value = getattr(FLAGS, name)
        if type(value) == float:
            print('\t%s: %f' % (name, value))
        elif type(value) == int:
            print('\t%s: %d' % (name, value))
        elif type(value) == str:
            print('\t%s: %s' % (name, value))
        elif type(value) == bool:
            print('\t%s: %s' % (name, value))
        else:
            print('\t%s: %s' % (name, value))

    print('End of configuration')


def update(it, image, image_d, image_white, axis):
    image_d = tf.cond(tf.less(tf.random.uniform([], minval=0, maxval=1), 0.5),
                      lambda: tf.concat([image_d, image_white], axis=axis),
                      lambda: tf.concat([image_d, image], axis=axis))
    it = it + 1

    return it, image, image_d, image_white, axis


def duplicate(image, times, axis_mode="height", mode="train"):
    times = tf.cast(times, dtype=tf.int32)
    if axis_mode == "height":
        axis = tf.constant(0)
        tile_shape = (times, 1, 1)
    elif axis_mode == "width":
        axis = tf.constant(1)
        tile_shape = (1, times, 1)
    else:
        raise ValueError("[ERROR]: Unknown mode for duplicate: " + axis_mode)

    if mode == "train":
        image_d = tf.identity(image)
        # image_white = tf.ones_like(image, dtype=tf.float32) * 0.999
        image_white = tf.random_uniform(tf.shape(image), minval=0.94, maxval=0.999, dtype=tf.float32)
        it = tf.constant(0)
        condition = lambda it, image, image_d, image_white, axis: tf.less(it, times - 1)
        _, _, image_d, _, _ = tf.while_loop(condition, update, (it, image, image_d, image_white, axis),
                                            shape_invariants=(it.get_shape(), tf.TensorShape([None, None, None]),
                                                              tf.TensorShape([None, None, None]),
                                                              tf.TensorShape([None, None, None]), axis.get_shape()))
    elif mode == "val":
        image_d = tf.tile(image, tile_shape)
    else:
        raise ValueError("[ERROR]: Unknown mode for duplicate: " + mode)

    return image_d


def shape(image):
    _shape = tf.shape(image)
    return _shape[0], _shape[1], _shape[2]


def process_singe_image(image_path, FLAGS):
    image = tf.read_file(image_path)
    image = tf.image.decode_png(image, channels=3)
    image = tf.image.convert_image_dtype(image, dtype=tf.float32)

    assertion = tf.assert_equal(tf.shape(image)[2], 3, message="image does not have 3 channels")
    with tf.control_dependencies([assertion]):
        image = tf.identity(image)

    # scale image, new min(height,  width) = FLAGS.image_size
    with tf.name_scope("scaling"):
        h, w, _ = shape(image)
        scale = tf.cast(FLAGS.image_size, dtype=tf.float32) / tf.cast(tf.cond(tf.less(h, w), lambda: w, lambda: h),
                                                                      dtype=tf.float32)
        image = tf.squeeze(tf.image.resize_bilinear(tf.expand_dims(image, 0), [
            tf.cast(tf.floor(scale * tf.cast(h, dtype=tf.float32)), dtype=tf.int32),
            tf.cast(tf.floor(scale * tf.cast(w, dtype=tf.float32)), dtype=tf.int32)]))
        image.set_shape([None, None, 3])

    with tf.name_scope("pad"):
        h2, w2, _ = shape(image)
        h_diff, w_diff = FLAGS.image_size - h2, FLAGS.image_size - w2

        # If uncomment, then add it control dependency
        # print = tf.Print(h_diff, [scale, scale_h, scale_w, h, w, h1, w1, h2, w2, h_diff, w_diff],
        #                  message="scale, h, w, h_diff, w_diff: ")

        assert_positive_hdiff = tf.assert_greater_equal(h_diff, 0)
        assert_positive_wdiff = tf.assert_greater_equal(w_diff, 0)
        with tf.control_dependencies([assert_positive_hdiff, assert_positive_wdiff]):
            image = tf.pad(image, ([0, h_diff], [0, w_diff], [0, 0]), constant_values=0.999)

    image.set_shape([FLAGS.image_size, FLAGS.image_size, 3])
    # image = tf.cast(image, dtype=tf.float32)
    return image


def pre_process(image_paths_tensor, FLAGS, mode='train'):
    with tf.variable_scope('pre-process', reuse=tf.AUTO_REUSE):
        image_batch = tf.map_fn(lambda image_path: process_singe_image(image_path, FLAGS), image_paths_tensor,
                                dtype=tf.float32)
        image_batch = tf.stack(image_batch, axis=0)
        print('[BATCH SHAPE]:', mode, image_batch.get_shape(), image_batch.dtype)
        return image_batch


def infer(net, image_path_tensor, FLAGS):
    with tf.variable_scope('infer'):
        images = pre_process(image_path_tensor, FLAGS, mode='val')
        return net.forward_pass(images)


def get_closest_emb_label(enrolled_emb_dic: dict, embedding_list, np_ord=2):
    labels = []
    for emb in embedding_list:
        min_dist = sys.maxsize
        closest_lab = None
        for l, l_emb in enrolled_emb_dic.items():
            dist = np.linalg.norm((emb - l_emb), ord=np_ord)
            if dist < min_dist:
                min_dist = dist
                closest_lab = l
        labels.append(closest_lab)
    return labels


def validate(sess: tf.Session, val_forward_pass, images_path_tensor_val, val_enroll_dict: dict, val_batch_dict: dict,
             FLAGS):
    enrolled_emb_dict = {}
    # _enroll_embeddings = enroll(val_forward_pass, images_path_tensor_val, FLAGS)
    # _embedding_list = infer(val_forward_pass, images_path_tensor_val, FLAGS)
    for l, images_paths in val_enroll_dict.items():
        _embeddings = sess.run(val_forward_pass, feed_dict={images_path_tensor_val: images_paths})
        enrolled_emb_dict[l] = np.mean(_embeddings, axis=0)

    labels = []
    predicted = []
    for l, images_paths in val_batch_dict.items():
        embedding_list = sess.run(val_forward_pass, feed_dict={images_path_tensor_val: images_paths})
        labels.extend([l] * len(embedding_list))
        predicted.extend(get_closest_emb_label(enrolled_emb_dict, embedding_list))

    return (np.array(labels) == np.array(predicted)).mean()
