#! /usr/bin/python

# Copyright (c) 2016, Socially Intelligent Machines Lab 
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of data_logger_bag nor the names of its 
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" 
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE 
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE 
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE 
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# Author: Vivian Chu, vchu@gatech.edu
'''
table_bag_data.py

This is the work horse class that actually takes bag file data and converts
it into HDF5 format depending on message type.

It currently supports a wide range of ROS messages, but does not support all messages
'''

import rospy
import rosbag
import numpy
import sys
import os
import tables
import subprocess
import yaml
import glob
from collections import defaultdict
import numpy as np

from geometry_msgs.msg import Pose2D, Wrench, Vector3, Point, Pose

class TableBagData():
    '''
    Class that actually stores the data away for later use
    '''
    def __init__(self, live_flag=False):

        self.all_data = defaultdict(dict)
        self.topic_types = dict()
        self.h5file = None 

        if live_flag:
            self.setup_internal_h5()

    def setup_internal_h5(self):

        #if self.h5file:
        #    self.h5file.close()

        filters = tables.Filters(complevel=9)
        self.h5file = tables.openFile('temp_data.h5', mode="w", title="Live Exploration Data",
                                 filters = filters)

        rospy.loginfo("Initialized internal h5 output file")

    def process_msg(self, topic, data):
        
        # Custom function that will process the messages depending on type
        #msg_type = self.topic_types[topic] 
        msg = data[0]
        stamp = data[1]

        try:
            msg_type = msg._type
            if msg_type == 'geometry_msgs/Wrench':
                self.process_wrench(topic, msg, stamp)

            elif msg_type == 'std_msgs/Int8':
                self.process_int8(topic, msg, stamp)

            elif msg_type == 'sensor_msgs/JointState':
                # We only pass in the msg and not the timestamp because the msg itself has
                # a time stamp in the header
                self.process_jointState(topic, msg, stamp)
                
            elif msg_type == 'rospcseg/ClusterArrayV0':
                self.process_clusterArray(topic, msg, stamp)
                
            elif msg_type == 'sensor_msgs/CameraInfo':
                self.process_cameraInfo(data)
                
            elif msg_type == 'std_msgs/String':
                self.process_string(topic, msg, stamp)
                
            elif msg_type == 'data_logger_bag/LogControl':
                self.process_logControl(topic, msg, stamp)

            elif msg_type == 'geometry_msgs/Pose2D':
                self.process_pose2D(topic, msg, stamp)
                
            elif msg_type == 'std_msgs/Bool':
                self.process_bool(topic, msg, stamp)

            elif msg_type == 'audio_common_msgs/AudioData':
                # Same message structure as Int8
                self.process_int8(topic, msg, stamp)

            elif msg_type == 'gait_capture/PersonFrame':
                self.process_gait(topic, msg, stamp)

            elif msg_type == 'cob_people_detection_msgs/DetectionArray':
                self.process_face_detect(topic, msg, stamp)
                #self.process_generic_msg(topic, msg, stamp)

            elif msg_type == 'bluetooth_capture/PingResult':
                self.process_bluetooth(topic, msg, stamp)

            elif msg_type == 'geometry_msgs/Pose':
                self.process_pose(topic, msg, stamp)

            else:
                rospy.logerr("Message type: %s is not supported" % msg_type)

        except:
            rospy.logerr("Error processing topic: %s " % topic)

    def msg_field_helper(self, msg):
        '''
        Used for custom messages that are not standard in ROS
        '''
       
        helper_fields = ['serialize', 'serialize_numpy', 'deserialize', 'deserialize_numpy', 'append', 'count', 'extend', 'index', 'insert', 'pop', 'remove', 'reverse', 'sort']
        fields = [name for name in dir(msg) if not (name.startswith('__') or name.startswith('_') or name in helper_fields)]
        return fields 

    def process_generic_msg(self, topic, msg, stamp):

        # Handle time
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []

        if hasattr(msg, 'header'):
            self.all_data[topic]['time'].append(msg.header.stamp.to_sec())
        else: 
            self.all_data[topic]['time'].append(stamp.to_sec())

        if 'data' not in self.all_data[topic]:
            self.all_data[topic]['data'] = []
        
        msg_dict = dict()
        for field in self.msg_field_helper(msg):
            msg_dict = self.generic_msg_recurse(eval('msg.'+field), field, msg_dict)

        self.all_data[topic]['data'].append(msg_dict)
        
    def generic_msg_recurse(self, msg, top_field, data_info):

        # Check if we are at the base of the message
        if not hasattr(msg,'_type') or len(self.msg_field_helper(msg)) < 1:
            data_info[top_field] = msg
            return data_info

        else:
            temp = dict()
            for field in self.msg_field_helper(msg):
                temp = self.generic_msg_recurse(eval('msg.'+field), field, temp)
            data_info[top_field] = temp
            return data_info

    def process_face_detect(self, topic, msg, stamp):

        # Store all of the timestamps by seconds from stamp
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []
        self.all_data[topic]['time'].append(msg.header.stamp.to_sec())

        msg_fields = ['label','detector','position','orientation','label_dist','dist_label']

        if len(msg.detections) > 0:
            # Compute the closest distance
            best_dist = 1000000000000000000000000.0
            best_label = ''
            best_msg = None

            # find the best face message
            for detect_msg in msg.detections:
                if detect_msg.detector == 'face':
                    dist_arr_msg = detect_msg.dists
                    for dist_msg in dist_arr_msg:
                        if dist_msg.distance < best_dist:
                                best_dist = dist_msg.distance
                                best_label = dist_msg.label
                                best_msg = detect_msg 

            if best_msg == None:

                # Will only get here once - if we never found a head
                for msg_field in msg_fields:
                    if msg_field not in self.all_data[topic]:
                        self.all_data[topic][msg_field] = []

                self.all_data[topic]['label'].append('')
                self.all_data[topic]['detector'].append(detect_msg.detector)
                self.all_data[topic]['position'].append([0.0, 0.0, 0.0])
                self.all_data[topic]['orientation'].append([0.0, 0.0, 0.0, 0.0])
                self.all_data[topic]['label_dist'].append(0.0)
                self.all_data[topic]['dist_label'].append('')

            else:
                for msg_field in msg_fields:
                    if msg_field not in self.all_data[topic]:
                        self.all_data[topic][msg_field] = []

                    if msg_field in ['position', 'orientation']:
                        data = []
                        data.append(eval('best_msg.pose.pose.'+msg_field+'.x'))
                        data.append(eval('best_msg.pose.pose.'+msg_field+'.y'))
                        data.append(eval('best_msg.pose.pose.'+msg_field+'.z'))

                        if msg_field == 'orientation':
                            data.append(eval('best_msg.pose.pose.'+msg_field+'.w'))
                       
                        self.all_data[topic][msg_field].append(data)

                    elif msg_field == 'label_dist':
                        self.all_data[topic][msg_field].append(best_dist)
                    elif msg_field == 'dist_label':
                        self.all_data[topic][msg_field].append(best_label)
                    else: 
                        self.all_data[topic][msg_field].append(eval('best_msg.'+msg_field))

        else:
            for msg_field in msg_fields:
                if msg_field not in self.all_data[topic]:
                    self.all_data[topic][msg_field] = []

            self.all_data[topic]['label'].append('')
            self.all_data[topic]['detector'].append('')
            self.all_data[topic]['position'].append([0.0, 0.0, 0.0])
            self.all_data[topic]['orientation'].append([0.0, 0.0, 0.0, 0.0])
            self.all_data[topic]['label_dist'].append(0.0)
            self.all_data[topic]['dist_label'].append('')
        
                
    def process_bluetooth(self, topic, msg, stamp):

        msg_fields = self.msg_field_helper(msg)
        for msg_field in msg_fields:
            if msg_field not in self.all_data[topic]:
                self.all_data[topic][msg_field] = []

            self.all_data[topic][msg_field].append(eval('msg.'+msg_field))

        # Store all of the timestamps by seconds from stamp
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []
        self.all_data[topic]['time'].append(msg.header.stamp.to_sec())

    def process_wrench(self, topic, msg, stamp):

        msg_fields = ['force','torque']
        for field in msg_fields:
            if field not in self.all_data[topic]:
                self.all_data[topic][field] = []

            data_store = []
            data_store.append(eval('msg.'+field+'.x'))
            data_store.append(eval('msg.'+field+'.y'))
            data_store.append(eval('msg.'+field+'.z'))
            self.all_data[topic][field].append(data_store)
        
        # Process timestamp - currently just seconds
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []

        self.all_data[topic]['time'].append(stamp.to_sec())

    def process_int8(self, topic, msg, stamp):

        #print "processing std_msgs/Int8 message"
        if 'data' not in self.all_data[topic]:
            self.all_data[topic]['data'] = []

        self.all_data[topic]['data'].append(msg.data)
       
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []

        self.all_data[topic]['time'].append(stamp.to_sec())

    def process_gait(self, topic, msg, stamp):
    
        # Pull out person ID 
        ID = msg.person_id

        # Check if nan...
        if np.isnan(ID):
            rospy.logerr("skipping nan person... Possible misalignment with gait") 
            return

        if ID not in self.all_data[topic]:
            self.all_data[topic][ID] = dict()

        if 'body_parts' not in self.all_data[topic][ID]:
            self.all_data[topic][ID]['body_parts'] = dict()

        time_fields = ['latest_time','time']
        for field in time_fields:
            if field not in self.all_data[topic][ID]:
                self.all_data[topic][ID][field] = []

        self.all_data[topic][ID]['time'].append(stamp.to_sec())
        self.all_data[topic][ID]['latest_time'].append(msg.latest_time.to_sec())

        # Parse the body parts and store
        data_fields = ['rotation','translation']
        for body_part in msg.body_parts:

            body_part_name = '_'.join(body_part.child_frame_id.split('_')[0:-1])[1::]

            # Check if the body part exists first
            if body_part_name not in self.all_data[topic][ID]['body_parts']:
                self.all_data[topic][ID]['body_parts'][body_part_name] = dict()
                self.all_data[topic][ID]['body_parts'][body_part_name]['rotation'] = []
                self.all_data[topic][ID]['body_parts'][body_part_name]['translation'] = []

            for field in data_fields: 
                data_store = []
                data_store.append(eval('body_part.transform.'+field+'.x'))
                data_store.append(eval('body_part.transform.'+field+'.y'))
                data_store.append(eval('body_part.transform.'+field+'.z'))

                # Put into dictionary 
                self.all_data[topic][ID]['body_parts'][body_part_name][field].append(data_store)

    def process_jointState(self, topic, msg, stamp):

        # Store the name only once
        if 'name' not in self.all_data[topic] and hasattr(msg, 'header'):
            self.all_data[topic]['name'] = msg.name

        # Store all of the timestamps by seconds from msg
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []

        if hasattr(msg, 'header'):
            self.all_data[topic]['time'].append(msg.header.stamp.to_sec())
        else:
            self.all_data[topic]['time'].append(stamp.to_sec())
            
        # Go through the actual fields of the msg and populate
        msg_fields = ['position','velocity','effort']
        for msg_field in msg_fields:
            if msg_field not in self.all_data[topic]:
                self.all_data[topic][msg_field] = []

            self.all_data[topic][msg_field].append(eval('msg.'+msg_field))

    def process_clusterArray(self, topic, msg, stamp):

        fields = self.msg_field_helper(msg.clusters[0])

        # Go through each cluster and turn into a single dict that we will convert
        clusters = []
        for one_cluster in msg.clusters:
            obj = dict()
            for field in fields:
                data = eval('one_cluster.'+field)
                if hasattr(data, '_type'):
                    if data._type == 'geometry_msgs/Vector3':
                        val = [data.x, data.y, data.z]
                    elif data._type == 'std_msgs/ColorRGBA':
                        val = [data.r, data.g, data.b, data.a]
                    else:
                        rospy.logerr("Unknown field type %s in msg type %s" % (data._type, msg.clusters[0]._type))
                else: 
                    val = data

                # Write the data to the field
                obj[field] = val
            clusters.append(obj)
     
        # Store the clusters away
        if 'clusters' not in self.all_data[topic]:
            self.all_data[topic]['clusters'] = []
        self.all_data[topic]['clusters'].append(clusters)

        # Store all of the timestamps by seconds from stamp
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []
        self.all_data[topic]['time'].append(stamp.to_sec())

        # Store the max number of objects seen 
        if 'max_item' not in self.all_data[topic]:
            cur_max = 1
        else:
            cur_max = self.all_data[topic]['max_item']

        self.all_data[topic]['max_item'] = max(cur_max,len(clusters))
            

    def process_cameraInfo(self, msg):

        #print "processing sensor_msgs/CameraInfo message"
        return

    def process_string(self, topic, msg, stamp):

        # It is currently the same for this type
        self.process_int8(topic, msg, stamp)

    def process_logControl(self, topic, msg, stamp):

        msg_fields = self.msg_field_helper(msg)
        for msg_field in msg_fields:
            if msg_field not in self.all_data[topic]:
                self.all_data[topic][msg_field] = []

            self.all_data[topic][msg_field].append(eval('msg.'+msg_field))

        # Store all of the timestamps by seconds from stamp
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []
        self.all_data[topic]['time'].append(stamp.to_sec())

    def process_pose2D(self, topic, msg, stamp):

        # It is the same as logControl
        self.process_logControl(topic, msg, stamp)


    def process_pose(self, topic, msg, stamp):

        msg_fields = ['position','orientation']
        for field in msg_fields:
            if field not in self.all_data[topic]:
                self.all_data[topic][field] = []

            data_store = []
            data_store.append(eval('msg.'+field+'.x'))
            data_store.append(eval('msg.'+field+'.y'))
            data_store.append(eval('msg.'+field+'.z'))
            if field == 'orientation':
                data_store.append(eval('msg.'+field+'.w'))

            self.all_data[topic][field].append(data_store)
        
        # Process timestamp - currently just seconds
        if 'time' not in self.all_data[topic]:
            self.all_data[topic]['time'] = []

        self.all_data[topic]['time'].append(stamp.to_sec())

    def process_bool(self, topic, msg, stamp):

        # It is currently the same for this type
        self.process_int8(topic, msg, stamp)

    def write_pytables(self, filename, fileCounter, bag_dir_group=None):

        # Setup the pytable names
        '''
        Start writing data to the table
        '''
        # TODO figure out naming scheme for groups
        group_name = os.path.splitext(filename)[0]
        path_name = os.path.split(group_name) # Split filepath from filename
        group_name = path_name[1] + '_'+path_name[0].split("/")[-1]
        #group_name = group_name+'_'+str(fileCounter) # Add simple counter for ID purposes later
        #group_name = "exploration_"+path_name[0].split("/")[-1]+"_"+str(fileCounter) 
        #group_name = ('_'.join(path_name[1].split('_')[0:-1])+"_"+str(fileCounter).zfill(3)).replace('-','_')
        group_name = ('_'.join(path_name[1].split('_')[0:-1])+"_"+str(fileCounter).zfill(3)).replace('-','_')

        # Add in custom name conversion 
        group_name = group_name.replace('.','Decimal')
        group_name = group_name.replace('[','LeftBracket')
        group_name = group_name.replace(']','RightBracket')
        group_name = group_name.replace(',','Comma')
        group_name = group_name.replace(':','Colon')
        
        rospy.loginfo("Writing file: %s to pytable as %s" % (path_name[1], group_name))

        if bag_dir_group is None:
            bag_group = self.h5file.createGroup("/", group_name)
        else:
            bag_group = self.h5file.createGroup(bag_dir_group, group_name)

        # Go through and write dictionary types into pytable
        for topic in self.all_data:
            msg_type = self.topic_types[topic]

            topic_name = '_'.join(topic.split('/'))
            topic_group = self.h5file.createGroup(bag_group, topic_name)
            data = self.all_data[topic]

            if msg_type == 'geometry_msgs/Wrench':
                self.write_wrench(topic_group, data)

            elif msg_type == 'std_msgs/Int8':
                self.write_int8(topic_group, data)

            elif msg_type == 'sensor_msgs/JointState':
                self.write_jointState(topic_group, data)
                
            elif msg_type == 'rospcseg/ClusterArrayV0':
                self.write_clusterArray(topic_group, data)
                
            elif msg_type == 'sensor_msgs/CameraInfo':
                self.write_cameraInfo(topic_group, data)
                
            elif msg_type == 'std_msgs/String':
                self.write_string(topic_group, data)
                
            elif msg_type == 'data_logger_bag/LogControl':
                self.write_logControl(topic_group, data)

            elif msg_type == 'geometry_msgs/Pose2D':
                self.write_pose2D(topic_group, data)

            elif msg_type == 'geometry_msgs/Pose':
                self.write_pose(topic_group, data)

            elif msg_type == 'std_msgs/Bool':
                self.write_bool(topic_group, data)

            elif msg_type == 'gait_capture/PersonFrame':
                self.write_gait(topic_group, data)
                
            elif msg_type == 'audio_common_msgs/AudioData':
                self.write_audio16(topic_group, data)

            elif msg_type == 'bluetooth_capture/PingResult':
                self.write_bluetooth(topic_group, data)

            elif msg_type == 'cob_people_detection_msgs/DetectionArray':
                self.write_face_detect(topic_group, data)

            else:
                rospy.logerr("Message type: %s is not supported" % msg_type)
        

    def write_wrench(self, topic_group, data):

        self.pytable_writer_helper(topic_group, data.keys(), tables.Float64Atom(), data)

    def write_int8(self, topic_group, data):

        self.pytable_writer_helper(topic_group, ['data'], tables.Int64Atom(), data)
        self.pytable_writer_helper(topic_group, ['time'], tables.Float64Atom(), data)

    def write_face_detect(self, topic_group, data):

        self.pytable_writer_helper(topic_group, ['label', 'dist_label', 'detector'], tables.StringAtom(itemsize=20), data)
        self.pytable_writer_helper(topic_group, ['position', 'orientation','label_dist','time'], tables.Float64Atom(), data)

    def write_audio16(self, topic_group, data):

        # Fix nan possibilities with the first value that is good
        # Currently not supported....
        '''
        if np.any(np.isnan(data['data'])):
            replace_idx = np.where(np.all(np.isnan(data['data']), axis=1))[0]
            good_idx = np.where(np.all(np.logical_not(np.isnan(data['data'])), axis=1))[0][0]
            data['data'][replace_idx] = data['data'][good_idx]
            data['time'][replace_idx] = data['time'][good_idx]
        '''

        # Convert to int16
        raw_audio = np.fromstring(''.join(data['data']), dtype=np.int16)

        # Pull out left and right audio
        # Warning: this might be flipped...(right/left)
        data['right_audio'], data['left_audio'] = raw_audio[0::2],raw_audio[1::2]

        self.pytable_writer_helper(topic_group, ['left_audio', 'right_audio'], tables.Int64Atom(), data)
        self.pytable_writer_helper(topic_group, ['time'], tables.Float64Atom(), data)

    def write_jointState(self, topic_group, data):

        fields = ['position', 'velocity', 'effort', 'time']
        self.pytable_writer_helper(topic_group, fields, tables.Float64Atom(), data)
        self.pytable_writer_helper(topic_group, ['name'], tables.StringAtom(itemsize=20), data)

    def write_gait(self, topic_group, data):

        fields = ['time','latest_time']
        for person_id in data:
            # Create a new group for each person
            person_group = self.h5file.createGroup(topic_group, 'user0'+str(person_id))
          
            # Write off time information
            self.pytable_writer_helper(person_group, fields, tables.Float64Atom(), data[person_id])
       
            # Create group for each body part 
            for body_part_name in data[person_id]['body_parts']:
                # Pull out data
                body_part_data = data[person_id]['body_parts'][body_part_name]
                body_part_group = self.h5file.createGroup(person_group, body_part_name)

                # Write off the transform information
                self.pytable_writer_helper(body_part_group, body_part_data.keys(), tables.Float64Atom(), body_part_data)


    def write_generic_msg(self, topic_group, data):

        data_dict = defaultdict(dict) 

    def write_clusterArray(self, topic_group, data):

        cluster_dict = defaultdict(dict)
        clusters = data['clusters']
        dummy_obj = clusters[0][0] # Just grab a some random object
        # Go through the supposed number of objects
        for i in xrange(data['max_item']):

            # Go through each timestamp of the objects
            for cluster_array in clusters:

                good_value = True
                # Check to see if the object number exists
                if i >= len(cluster_array):
                    good_value = False
                    obj = dummy_obj
                else:
                    obj = cluster_array[i]
                
                # Go through the msg fields in the objects
                for field in obj:

                    # Check if field exists in the dictionary first
                    if field not in cluster_dict[i]:
                        cluster_dict[i][field] = []
                    
                    # Store NaNs if there was no object
                    if good_value is False:
                        if isinstance(dummy_obj[field],float):
                            dummy_vals = np.nan
                            cluster_dict[i][field].append(dummy_vals)
                        else:
                            dummy_vals = np.empty(np.shape(dummy_obj[field]))
                            dummy_vals[:] = np.nan
                            cluster_dict[i][field].append(dummy_vals.tolist())
                    else:
                        cluster_dict[i][field].append(obj[field])

        # populate the structure now
        for i in xrange(data['max_item']):
            single_obj = cluster_dict[i]
            obj_group = self.h5file.createGroup(topic_group, 'object_'+str(i))
            self.pytable_writer_helper(obj_group, single_obj.keys(), tables.Float64Atom(), single_obj)

    def write_cameraInfo(self, topic_group, data):
        return

    def write_string(self, topic_group, data):

        self.pytable_writer_helper(topic_group, ['data'], tables.StringAtom(itemsize=20), data)
        self.pytable_writer_helper(topic_group, ['time'], tables.Float64Atom(), data)

    def write_logControl(self, topic_group, data):

        fields = ['taskName', 'actionType', 'skillName', 'topics']
        self.pytable_writer_helper(topic_group, fields, tables.StringAtom(itemsize=20), data)
        self.pytable_writer_helper(topic_group, ['playback'], tables.BoolAtom(), data)

    def write_bluetooth(self, topic_group, data):

        str_fields = ['mac_addr', 'dev_name']
        self.pytable_writer_helper(topic_group, str_fields, tables.StringAtom(itemsize=20), data)
        self.pytable_writer_helper(topic_group, ['is_present'], tables.BoolAtom(), data)
        self.pytable_writer_helper(topic_group, ['rssi'], tables.Int64Atom(), data)
        self.pytable_writer_helper(topic_group, ['time'], tables.Float64Atom(), data)

    def write_pose2D(self, topic_group, data):

        fields = ['x', 'y', 'theta', 'time']
        self.pytable_writer_helper(topic_group, fields, tables.Float64Atom(), data)

    def write_pose(self, topic_group, data):

        self.pytable_writer_helper(topic_group, data.keys(), tables.Float64Atom(), data)

    def write_bool(self, topic_group, data):

        self.pytable_writer_helper(topic_group, ['data'], tables.BoolAtom(), data)
        self.pytable_writer_helper(topic_group, ['time'], tables.Float64Atom(), data)

    def pytable_writer_helper(self, topic_group, fields, data_type, data):

        # Go through the fields and write to the group
        for field in fields:
            data_size = np.shape(data[field])
            carray = self.h5file.createCArray(topic_group, field, data_type, data_size)
            carray[:] = data[field]


