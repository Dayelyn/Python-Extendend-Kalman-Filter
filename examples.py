#!/usr/bin/env python
# The MIT License (MIT)

# Copyright (c) 2014 xaedes

# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


import rospy
import numpy as np
# import numdifftools as nd   # sudo pip install numdifftools

from time import time

from std_msgs.msg import Int32
from std_msgs.msg import Float32
from geometry_msgs.msg import Vector3Stamped
from sensor_msgs.msg import Imu

import functools
import sys
import signal

import threading

from Kalman import Kalman
from ExtendedKalman import ExtendedKalman
from DTFilter import DTFilter

class ImuSensorsFilter(Kalman):
    """docstring for ImuSensorsFilter"""
    def __init__(self):
        super(ImuSensorsFilter, self).__init__(n_states = 9, n_sensors = 9)
        #states:
        #  accel: x,y,z     0:3
        #  gyro:  x,y,z     3:6
        #  mag:   x,y,z     6:9        

        #sensors:
        #  accel: x,y,z     0:3
        #  gyro:  x,y,z     3:6
        #  mag:   x,y,z     6:9        

        self.states = self.sensors = {'accel.x': 0, 'accel.y': 1, 'accel.z': 2,
                                      'gyro.x':  3, 'gyro.y':  4, 'gyro.z':  5,
                                      'mag.x':   6, 'mag.y':   7, 'mag.z':   8}

        # H: Messmatrix
        self.H = np.matrix( '1 0 0 0 0 0 0 0 0;'    #accel.x
                            '0 1 0 0 0 0 0 0 0;'    #accel.y
                            '0 0 1 0 0 0 0 0 0;'    #accel.z
                            '0 0 0 1 0 0 0 0 0;'    #gyro.x
                            '0 0 0 0 1 0 0 0 0;'    #gyro.y
                            '0 0 0 0 0 1 0 0 0;'    #gyro.z
                            '0 0 0 0 0 0 1 0 0;'    #mag.x
                            '0 0 0 0 0 0 0 1 0;'    #mag.y
                            '0 0 0 0 0 0 0 0 1 '    #mag.z
                            )
        # F: Dynamik
        self.F = np.matrix([[1,0,0,0,0,0,0,0,0],    #accel.x = accel.x
                            [0,1,0,0,0,0,0,0,0],    #accel.y = accel.y
                            [0,0,1,0,0,0,0,0,0],    #accel.z = accel.z
                            [0,0,0,1,0,0,0,0,0],    #gyro.x = gyro.x
                            [0,0,0,0,1,0,0,0,0],    #gyro.y = gyro.y
                            [0,0,0,0,0,1,0,0,0],    #gyro.z = gyro.z
                            [0,0,0,0,0,0,1,0,0],    #mag.x = mag.x
                            [0,0,0,0,0,0,0,1,0],    #mag.y = mag.y
                            [0,0,0,0,0,0,0,0,1]     #mag.z = mag.z
                            ])

        # Q: Unsicherheit der Dynamik 
        self.Q = np.matrix(np.identity(self.n_states)) * 0.1    
             
        # P: Unsicherheit ueber Systemzustand   
        self.P *= 0.1

        # R: Messunsicherheit
        self.R *= 1


    def measure(self,imu,mag,biases=None):
        Z = np.matrix([imu.linear_acceleration.x,imu.linear_acceleration.y,imu.linear_acceleration.z,
                       imu.angular_velocity.x,imu.angular_velocity.y,imu.angular_velocity.z,
                       mag.vector.x,mag.vector.y,mag.vector.z]).getT()

        self.predict()
        if biases==None:
            self.update(Z)
        else:
            self.update(Z-biases)

class ImuSensorsBiasFilter(ImuSensorsFilter):
    """docstring for ImuSensorsBiasFilter"""
    def __init__(self):
        super(ImuSensorsBiasFilter, self).__init__()

        # Aenderungen gegenueber ImuSensorsFilter:

        # Q: Unsicherheit der Dynamik 
        self.Q *= 1
             
        # P: Unsicherheit ueber Systemzustand   
        self.P *= 1

        # R: Messunsicherheit
        self.R *= 1
 

 
class MotionModelCV(ExtendedKalman):
    """Constant Velocity linear motion model"""
    #http://www.isif.org/fusion/proceedings/fusion08CD/papers/1569107835.pdf
    def __init__(self, dt):
        super(MotionModelCV, self).__init__(n_states = 4, n_sensors = 2)
        #states:
        #  acceleration  0
        #  velocity      1
        #  velocity_bias 2
        #  distance      3

        #sensors:
        #  acceleration  0
        #  velocity_bias 1

        self.states = {'acceleration': 0, 'velocity': 1, 'velocity_bias': 2, 'distance': 3}
        self.sensors = {'acceleration': 0,'velocity_bias': 1}

        # self.x[1,0] = 10

        self.dt = dt
        # F: Dynamik
        self.f = lambda x,u: np.array([
            [0],                                                                    # acceleration  = 0
            [x[1,0] + self.dt*x[0,0]],                                              # velocity      = velocity + dt * acceleration
            [x[2,0]],                                                               # velocity_bias = velocity_bias
            [self.dt*self.dt*x[0,0] + self.dt*x[1,0] - self.dt*x[2,0] + x[3,0]]     # distance      = distance + dt * (velocity-velocity_bias) + dt * dt * acceleration
            ])

        self.J_f_fun = lambda x: np.matrix([
            [0,0,0,0],
            [self.dt,1,0,0],
            [0,0,1,0],
            [self.dt*self.dt,self.dt,-self.dt,1]
            ])

        # h: Messfunktion
        # function h can be used to compute the predicted measurement from the predicted state
        self.h = lambda x: np.array([
            [0],  # expect to see zero 
            [0]   # expect to see zero 
            ])

        self.J_h_fun = lambda x: np.matrix([
            [0,0,0,0],
            [0,0,0,0]
            ])
    def update_dt(self,dt):
        self.dt = dt

    def measure(self,acceleration,zero_velocity):
        # print acceleration
        # Z = np.matrix([[acceleration]])
        if(zero_velocity==True):
            Z = np.matrix([acceleration,self.x[self.states['velocity'],0]]).getT()
        else:
            Z = np.matrix([acceleration,0]).getT()

        self.predict()

        self.x[0,0] = acceleration
        
        self.update(Z)


class OrientationFilter(ExtendedKalman):
    """OrientationFilter"""
    #
    def __init__(self, dt):
        super(OrientationFilter, self).__init__(n_states = 2, n_sensors = 1)
        #states:
        #  gyro  0
        #  angle 1

        #sensors:
        #  gyro  0

        self.states = {'gyro': 0, 'angle': 1}
        self.sensors = {'gyro': 0}

        self.dt = dt

        # F: Dynamik
        self.f = lambda x,u: np.array([
            [0],                                                                    # gyro  = 0
            [self.dt*x[0,0] + x[1,0]]                                              # angle = angle + dt * gyro
            ])

        self.J_f_fun = lambda x: np.matrix([
            [0,0],
            [1.,0]
            ])

        # h: Messfunktion
        # function h can be used to compute the predicted measurement from the predicted state
        self.h = lambda x: np.array([
            [0]   # expect to see zero 
            ])

        self.J_h_fun = lambda x: np.matrix([
            [0,0]
            ])
    def update_dt(self,dt):
        self.dt = dt

    def measure(self,gyro):
        Z = np.matrix([gyro]).getT()

        self.predict()

        self.x[0,0] = gyro  # hack
        
        self.update(Z)

class MeasureSampleRate(object):
    """docstring for MeasureSampleRate"""
    def __init__(self, update_interval = 10, gain = 0.5):
        super(MeasureSampleRate, self).__init__()
        self.sample_rate = 1
        self.gain = gain
        self.update_interval = update_interval #in number of samples
        self.first_samplerate = False
        self.n_samples = 0
        self.last_time = rospy.Time.now().to_sec()

    def add_sample(self):
        self.n_samples = (self.n_samples + 1) % self.update_interval
        if self.n_samples == 0:
            self.update_sample_rate(self.update_interval)

    def update_sample_rate(self, n_new_samples = 1):
        dtime = rospy.Time.now().to_sec() - self.last_time
        self.last_time = rospy.Time.now().to_sec()

        current_sample_rate = n_new_samples / dtime

        if self.first_samplerate:
            self.first_samplerate = False
            self.sample_rate = current_sample_rate

        self.sample_rate = self.sample_rate * (1-self.gain) + current_sample_rate * self.gain        

        return self.sample_rate

    def __complex__(self):
        return complex(self.sample_rate)
    def __int__(self):
        return int(self.sample_rate)
    def __long__(self):
        return long(self.sample_rate)
    def __float__(self):
        return float(self.sample_rate)

class Subscriber(object):
    """docstring for Subscriber"""
    def __init__(self):
        super(Subscriber, self).__init__()
        rospy.init_node('kalman', anonymous=True)


        self.run = True
        self.pause = False
        self.rate = 40.
        signal.signal(signal.SIGINT, self.keyboard_interupt)
        
        self.dt = 1./min(self.rate,40.) # topic rate ~ 40hz

        self.measure_sample_rate = MeasureSampleRate()

        self.sensors = ImuSensorsFilter()
        self.sensor_biases = ImuSensorsBiasFilter()

        self.orientation = OrientationFilter(dt=self.dt)

        self.motion_cv = MotionModelCV(dt=self.dt)

        # Publishers
        self.pub_imu = rospy.Publisher('/imu/data_filtered', Imu)
        self.pub_imu_bias = rospy.Publisher('/imu/data_biases', Imu)
        self.pub_mag = rospy.Publisher('/imu/mag_filtered', Vector3Stamped)
        self.pub_rps = rospy.Publisher('/rps', Float32)
        self.pub_dt = rospy.Publisher('/kalman_dt', Float32)

        self.pub_cv_vel = rospy.Publisher('/motion/cv/velocity', Float32)
        self.pub_cv_acc = rospy.Publisher('/motion/cv/acceleration', Float32)
        self.pub_cv_dis = rospy.Publisher('/motion/cv/distance', Float32)

        self.pub_orientation = rospy.Publisher('/orientation', Float32)

        # Subscribers
        rospy.Subscriber('/imu/data_raw', Imu, self.callback_imu)
        rospy.Subscriber('/imu/mag', Vector3Stamped, self.callback_mag)
        rospy.Subscriber('/sensor_motor_revolutions', Int32, self.callback_revolutions)
        rospy.Subscriber('/sensor_motor_rps', Float32, self.callback_rps)

        self.rps_gain = 0.25
        self.dt_gain = 0.125 / 2

        self.dtfilter = DTFilter(dt_estimate = 1.0 / self.rate)

        self.imu = self.mag = self.revolutions = None
        self.rps = 0
        self.last_revolutions = self.last_rps_time = self.last_time = None


        self.counter = 0

        self.spin()

    def reset(self):
        self.dtfilter = DTFilter(dt_estimate = 1.0 / self.rate)
        self.dt = self.dtfilter.update()
        
        self.sensors.reset()
        self.sensor_biases.reset()
        self.motion_cv.reset()
        self.orientation.reset()

        self.imu = self.mag = self.revolutions = None
        self.rps = 0
        self.last_revolutions = self.last_rps_time = self.last_time = None

        print "reset"

    def callback_rps(self, msg):
        self.rps = msg.data

    def callback_revolutions(self, msg):
        self.revolutions = msg.data

    def callback_imu(self, msg):
        self.imu = msg

    def callback_mag(self, msg):
        self.mag = msg

    def spin(self):
        # print "Setting up rate ", self.rate, "hz"
        r = rospy.Rate(self.rate)
        while(self.run):
            self.measure()
            # self.pub_mag.publish(Vector3Stamped())
            r.sleep()


    def measure(self):
        # only proceed if we have all msgs
        if((self.mag==None)or(self.imu==None)or(self.rps==None)):
            return

        # update sample rate and dt
        self.measure_sample_rate.add_sample()
        # self.dt = 1/float(self.measure_sample_rate)

        # filter dt
        self.dt = self.dtfilter.update()

        # self.pub_dt.publish(self.dt)

        self.counter += 1
        if(self.counter==10):
            print float(self.measure_sample_rate)
            self.counter=0

        # update bias if car stands still (rps < 1)
        if(self.rps < 1):
            self.sensor_biases.measure(self.imu,self.mag)

        # update sensors
        self.sensors.measure(self.imu,self.mag,self.sensor_biases.x)

        # update orientation filter
        self.orientation.dt = self.dt
        self.orientation.measure(self.sensors.x[self.sensors.states['gyro.z'],0])

        # grab header to propagate to published msgs
        header = self.mag.header

        # update motion model

        # self.motion_cv.update_dt(self.dt)
        # self.motion_cv.measure(self.sensors.x[self.sensors.states['accel.x'],0],)

        ######### publish filtered data

        self.pub_orientation.publish(self.orientation.x[self.orientation.states['angle'],0])

        # if(False):
        #     # publish imu bias
        #     imu = Imu()
        #     imu.header = header
        #     imu.linear_acceleration.x = self.sensor_biases.x[0]
        #     imu.linear_acceleration.y = self.sensor_biases.x[1]
        #     imu.linear_acceleration.z = self.sensor_biases.x[2]
        #     imu.angular_velocity.x = self.sensor_biases.x[3]
        #     imu.angular_velocity.y = self.sensor_biases.x[4]
        #     imu.angular_velocity.z = self.sensor_biases.x[5]
        #     self.pub_imu_bias.publish(imu)

        #     # publish filtered mag
        #     mag = Vector3Stamped()
        #     mag.header = header
        #     mag.vector.x = self.sensors.x[6]
        #     mag.vector.y = self.sensors.x[7]
        #     mag.vector.z = self.sensors.x[8]
        #     self.pub_mag.publish(mag)

        #     # publish rps
        #     self.pub_rps.publish(self.rps)

        #     # publish filtered imu
        #     imu = Imu()
        #     imu.header = header
        #     imu.linear_acceleration.x = self.sensors.x[0,0]
        #     imu.linear_acceleration.y = self.sensors.x[1,0]
        #     imu.linear_acceleration.z = self.sensors.x[2,0]
        #     imu.angular_velocity.x = self.sensors.x[3,0]
        #     imu.angular_velocity.y = self.sensors.x[4,0]
        #     imu.angular_velocity.z = self.sensors.x[5,0]
        #     self.pub_imu.publish(imu)

        # # publish data from motion model 
        # self.pub_cv_vel.publish(self.motion_cv.x[self.motion_cv.states['velocity'],0]-self.motion_cv.x[self.motion_cv.states['velocity_bias'],0])
        # self.pub_cv_acc.publish(self.motion_cv.x[self.motion_cv.states['acceleration'],0])
        # self.pub_cv_dis.publish(self.motion_cv.x[self.motion_cv.states['distance'],0])

        # remove old msgs
        self.imu = self.mag = self.rps = None



    def keyboard_interupt(self, signum, frame):
        self.run = False
        print " closing...\n"


if __name__ == '__main__':
    subscriber = Subscriber()


