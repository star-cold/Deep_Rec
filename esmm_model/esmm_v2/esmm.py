#!/data/venv/hdp-env/bin python
# -*- coding: utf8 -*-
# @Author  : shixiangfu
import tensorflow as tf
import sys
sys.path.append("..")
from tensorflow.python.ops import string_ops,array_ops,math_ops
from tensorflow.python.saved_model import signature_constants
from tensorflow.python.estimator.export import export_output
from transform_feature import FeatureBuilder
from alg_utils.utils_tf import PReLU,get_input_schema_spec
import random
class esmm(object):
    def __init__(self, features,labels,params,mode):
        self.features = features
        self.labels =labels
        self.params = params
        self.mode = mode
        self.model_features = params["FEATURES_DICT"]

    def get_feature_columns(self):
        Feature_Columns = FeatureBuilder(self.model_features)
        _,DeepFeatures = Feature_Columns.get_feature_columns()
        return DeepFeatures

    def _classification_output(self,scores, n_classes, label_vocabulary=None):
        batch_size = array_ops.shape(scores)[0]
        if label_vocabulary:
            export_class_list = label_vocabulary
        else:
            export_class_list = string_ops.as_string(math_ops.range(n_classes))
        export_output_classes = array_ops.tile(
            input=array_ops.expand_dims(input=export_class_list, axis=0),
            multiples=[batch_size, 1])
        return export_output.ClassificationOutput(
            scores=scores,
            # `ClassificationOutput` requires string classes.
            classes=export_output_classes)
    def Din_model(self):
        '''din model to do'''
        pass

    def Dnn_Model(self,feature_columns):
        '''dnn model'''
        net = tf.feature_column.input_layer(self.features, feature_columns)
        # Build the hidden layers, sized according to the 'hidden_units' param.
        for units in self.params['HIDDEN_UNITS']:
            net = tf.layers.dense(net, units=units, activation=PReLU)
            if 'DROPOUT_RATE' in self.params and self.params['DROPOUT_RATE'] > 0.0:
                net = tf.layers.dropout(net, self.params['DROPOUT_RATE'], training=(self.mode == tf.estimator.ModeKeys.TRAIN))
        logits = tf.layers.dense(net, 1, activation=None)
        return logits

    def Build_EstimatorSpec(self):
        '''Build EstimatorSpec'''
        with tf.variable_scope('embedding_module'):
            feature_columns = self.get_feature_columns()
            print("feature_columns:", feature_columns)
        with tf.variable_scope('ctr_model'):
            ctr_logits = self.Dnn_Model(feature_columns)
        with tf.variable_scope('cvr_model'):
            cvr_logits = self.Dnn_Model(feature_columns)

        ctr_predictions = tf.sigmoid(ctr_logits, name="CTR")
        cvr_predictions = tf.sigmoid(cvr_logits, name="CVR")
        prop = tf.multiply(ctr_predictions, cvr_predictions, name="CTCVR")
        if self.mode == tf.estimator.ModeKeys.PREDICT:
            CLASSES = 'classes'
            CLASS_IDS = 'class_ids'
            two_class_ctcvr_prob = tf.concat(
                (tf.subtract(1.0, prop), prop),
                # (array_ops.zeros_like(ctcvr_prob), ctcvr_prob),
                axis=-1, name='two_class_logits')
            class_ids = tf.argmax(two_class_ctcvr_prob, axis=-1, name=CLASS_IDS)
            class_ids = tf.expand_dims(class_ids, axis=-1)

            classes = tf.as_string(class_ids, name='str_classes')
            classifier_output = self._classification_output(
                scores=two_class_ctcvr_prob, n_classes=2,
                label_vocabulary=None)
            predictions = {
                'probabilities': prop,
                CLASS_IDS: class_ids,
                CLASSES: classes,
                'ctr_probabilities': ctr_predictions,
                'cvr_probabilities': cvr_predictions
            }
            _DEFAULT_SERVING_KEY = signature_constants.DEFAULT_SERVING_SIGNATURE_DEF_KEY
            _CLASSIFY_SERVING_KEY = 'classification'
            _REGRESS_SERVING_KEY = 'regression'
            _PREDICT_SERVING_KEY = 'predict'
            export_outputs = {
                _DEFAULT_SERVING_KEY: classifier_output,
                _CLASSIFY_SERVING_KEY: classifier_output,
                _PREDICT_SERVING_KEY: tf.estimator.export.PredictOutput(predictions)
            }
            return tf.estimator.EstimatorSpec(self.mode, predictions=predictions, export_outputs=export_outputs)

        y = self.labels['cvr']
        cvr_loss = tf.reduce_sum(tf.keras.backend.binary_crossentropy(tf.reshape(y,(-1,1)), prop), name="cvr_loss")
        ctr_loss = tf.reduce_sum(tf.nn.sigmoid_cross_entropy_with_logits(labels=tf.reshape(self.labels['ctr'],(-1,1)), logits=ctr_logits),
                                 name="ctr_loss")
        loss = tf.add(ctr_loss, cvr_loss, name="ctcvr_loss")

        ctr_accuracy = tf.metrics.accuracy(labels=self.labels['ctr'],
                                           predictions=tf.to_float(tf.greater_equal(ctr_predictions, 0.5)))
        cvr_accuracy = tf.metrics.accuracy(labels=y, predictions=tf.to_float(tf.greater_equal(prop, 0.5)))
        ctr_auc = tf.metrics.auc(self.labels['ctr'], ctr_predictions)
        cvr_auc = tf.metrics.auc(y, prop)

        # ctcvr_auc = tf.metrics.auc(tf.reshape(label_cvr, (-1, 1)), ctcvr_prob)
        # ctr_recall = tf.metrics.recall(labels=tf.reshape(label_ctr, (-1, 1)),
        #                                predictions=tf.to_float(tf.greater_equal(ctr_prob, 0.5)))
        # cvr_recall = tf.metrics.recall(labels=tf.reshape(label_cvr, (-1, 1)),
        #                                predictions=tf.to_float(tf.greater_equal(cvr_prob, 0.5)))
        # ctcvr_recall = tf.metrics.recall(labels=tf.reshape(label_cvr, (-1, 1)),
        #                                  predictions=tf.to_float(tf.greater_equal(ctcvr_prob, 0.5)))
        metrics = {'cvr_accuracy': cvr_accuracy, 'ctr_accuracy': ctr_accuracy, 'ctr_auc': ctr_auc, 'cvr_auc': cvr_auc}
        tf.summary.scalar('ctr_accuracy', ctr_accuracy[1])
        tf.summary.scalar('cvr_accuracy', cvr_accuracy[1])
        tf.summary.scalar('ctr_auc', ctr_auc[1])
        tf.summary.scalar('cvr_auc', cvr_auc[1])
        if self.mode == tf.estimator.ModeKeys.EVAL:
            return tf.estimator.EstimatorSpec(self.mode, loss=loss, eval_metric_ops=metrics)

        # Create training op.
        assert self.mode == tf.estimator.ModeKeys.TRAIN
        optimizer = tf.train.AdagradOptimizer(learning_rate=self.params['LEARNING_RATE'])
        train_op = optimizer.minimize(loss, global_step=tf.train.get_global_step())
        return tf.estimator.EstimatorSpec(self.mode, loss=loss, train_op=train_op)


class export_model(object):
    '''to do'''
    def __init__(self,model=None,input_schema=None,servable_model_dir=None,drop_cols = ['click', 'buy']):
        self.model = model
        self.input_schema = input_schema
        self.servable_model_dir =servable_model_dir
        self.drop_cols = drop_cols

    def export(self):
        feature_spec = get_input_schema_spec(self.input_schema)
        for col in self.drop_cols:
            del feature_spec[col]
        export_input_fn = tf.estimator.export.build_parsing_serving_input_receiver_fn(feature_spec)
        servable_model_path = self.model.export_savedmodel(self.servable_model_dir, export_input_fn)
        print("*********** Done Exporting at PAth - %s", servable_model_path)




class testw(object):
    '''test case'''
    def __init__(self):
        self.a = 1
    def build_mode(self):
        rand = random.random()
        return rand
        # print(rand)
    def mymodel(self):
        a = self.build_mode()
        b = self.build_mode()
        print(a)
        print(b)
if __name__ == '__main__':
    ss =testw()
    ss.mymodel()