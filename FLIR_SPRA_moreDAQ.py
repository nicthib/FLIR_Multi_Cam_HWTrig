import os
import time
import threading
import PySpin
import serial
import sys
import nidaqmx
import numpy as np
import scipy.io as sio
import matplotlib.pyplot as plt
import ruamel.yaml
from pathlib import Path


# Personal verison for Hillman lab
def read_config(configname):
    """
    Reads structured config file
    """
    ruamelFile = ruamel.yaml.YAML()
    path = Path(configname)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                cfg = ruamelFile.load(f)
        except Exception as err:
            pass
    else:
        raise FileNotFoundError ("Config file is not found. Please make sure that the file exists and/or there are no unnecessary spaces in the path of the config file!")
    return(cfg)

# Change cwd to script folder
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

# Read cfg file
cfg = read_config('params_WFOM.yaml')
num_images = cfg['num_images']
run_length = cfg['run_length']
exp_time = cfg['exp_time']
bin_val = int(1)  # bin mode (WIP)
im_savepath = cfg['file_path'].replace('CCD', 'webcam') + '\\'
aux_savepath = cfg['file_path'].replace('CCD', 'auxillary') + '\\'
filename = cfg['file_name'] + str(cfg['stim_run'])
framerate = cfg['framerate']

# This makes the terminal nicely sized
if cfg['small_console'] == 1:
    os.system('mode con: cols=60 lines=16')

# Create webcam and aux save folder
if not os.path.exists(im_savepath):
    os.makedirs(im_savepath)
if not os.path.exists(aux_savepath):
    os.makedirs(aux_savepath)
os.chdir(im_savepath)

# Com port for Arduino communication
COM_port = 'COM10'
COM_baud = 115200

# Set up auxiliary behavior collection
try:
    ser = serial.Serial(COM_port, COM_baud)
    ser_avail = 1
    print('Serial available.')
except serial.SerialException:
    print('Serial port ' + COM_port + ' not available. No auxiliary behavior will be recorded.')
    ser_avail = 0

# Set up DAQ
DAQ_online = 0
if int(sys.argv[1]) == 1:
    try:
        fs = 10**4
        DAQ_ns = int(fs * run_length)
        ai_task = nidaqmx.Task()
        ao_task = nidaqmx.Task()
        ai_task.ai_channels.add_ai_voltage_chan(physical_channel='/Dev2/ai0', min_val=0, max_val=5)
        ai_task.ai_channels.add_ai_voltage_chan(physical_channel='/Dev2/ai1', min_val=0, max_val=5)
        ai_task.ai_channels.add_ai_voltage_chan(physical_channel='/Dev2/ai2', min_val=0, max_val=5)
        ai_task.ai_channels.add_ai_voltage_chan(physical_channel='/Dev2/ai4', min_val=0, max_val=5)
        ai_task.ai_channels.add_ai_voltage_chan(physical_channel='/Dev2/ai5', min_val=0, max_val=5)

        ao_task.ao_channels.add_ao_voltage_chan('/Dev2/ao0')
        ao_task.timing.cfg_samp_clk_timing(fs,
                                           sample_mode=nidaqmx.constants.AcquisitionType.FINITE,
                                           samps_per_chan=DAQ_ns)
        ai_task.timing.cfg_samp_clk_timing(fs,
                                           sample_mode=nidaqmx.constants.AcquisitionType.FINITE,
                                           samps_per_chan=DAQ_ns)
        # Load stim file
        if cfg['stim'] != 'off':
            mat_contents = sio.loadmat(r'C:\FLIR_Multi_Cam_HWTrig\stimfiles\stim'+str(cfg['stim'])+'.mat')
            stim = np.squeeze(mat_contents['DAQout'])
            ao_task.write(stim, auto_start=False)
            print('DAQ setup successful. Stim is ENABLED')

        else:
            ao_task.write(np.linspace(0, 0, DAQ_ns), auto_start=False)
            print('DAQ setup successful. Stim is DISABLED')

        DAQ_online = 1
    except:
        print('DAQ setup unsuccessful. No DAQ data will be recorded')


# Thread process for saving images. This is super important, as the writing process takes time inline,
# so offloading it to separate CPU threads allows continuation of image capture
class ThreadWrite(threading.Thread):
    def __init__(self, data, out):
        threading.Thread.__init__(self)  
        self.data = data
        self.out = out

    def run(self):
        #image_result = self.data
        #image_converted = image_result.Convert(PySpin.PixelFormat_Mono8, PySpin.HQ_LINEAR)
        self.data.Save(self.out)

# Capturing is also threaded, to increase performance
class ThreadCapture(threading.Thread):
    def __init__(self, cam, camnum, nodemap):
        threading.Thread.__init__(self)
        self.cam = cam
        self.camnum = camnum

    def run(self):
        times = []
        t1 = []
        stimstate = 'OFF'
        if framerate != 'hardware':
            nodemap = self.cam.GetNodeMap()

        if self.camnum == 0:
            primary = 1
            rotary_data = []
        else:
            primary = 0

        for i in range(num_images):
            fstart = time.time()
            try:
                #  Retrieve next received image
                if framerate == 'hardware':
                    image_result = self.cam.GetNextImage()
                else:
                    node_softwaretrigger_cmd = PySpin.CCommandPtr(nodemap.GetNode('TriggerSoftware'))
                    if not PySpin.IsAvailable(node_softwaretrigger_cmd) or not PySpin.IsWritable(
                            node_softwaretrigger_cmd):
                        print('Unable to execute trigger. Aborting...')
                        return False
                    node_softwaretrigger_cmd.Execute()
                    image_result = self.cam.GetNextImage()

                times.append(time.time())
                if i == 0 and primary == 1:
                    t1 = time.time()
                    print('*** ACQUISITION STARTED ***\n')
                    if DAQ_online:
                        ao_task.start()
                        ai_task.start()
                if i == int(num_images - 1) and primary:
                    t2 = time.time()
                if primary:
                    # Read serial values (primary cam only)
                    if ser_avail:
                        ser.readline()  # have to do this twice to get a full line
                        rotary_data.append(ser.readline())
                        ser.flushInput()

                    # Determine if stim is on or off
                    try:
                        if stim[int((time.time()-t1)*fs)] > 0:
                            stimstate = 'ON '
                        else:
                            stimstate = 'OFF'
                    except:
                        pass

                    # Display progress
                    print('COLLECTING {} of {}, time = {} sec, stim is {}'.format(str(i+1), str(num_images), str(int(time.time()-t1)), stimstate), end='\r')
                    sys.stdout.flush()

                fullfilename = filename + '_' + str(i+1) + '_cam' + str(primary) + '.jpg'
                background = ThreadWrite(image_result, fullfilename)
                background.start()
                image_result.Release()
                ftime = time.time() - fstart
                if framerate != 'hardware':
                    if ftime < 1/framerate:
                        time.sleep(1/framerate - ftime)

            except PySpin.SpinnakerException as ex:
                print('Error (577): %s' % ex)
                return False

        self.cam.EndAcquisition()
        if primary:
            print('Effective frame rate: ' + str(num_images / (t2 - t1)))

        # Save frametime data
        sio.savemat(os.path.join(aux_savepath,'t'+str(self.camnum)+'.mat'), {'t'+str(self.camnum): np.asarray(times)})
        if primary and ser_avail:
                # Save rotary data
                aux_data = []
                for item in rotary_data:
                    try:
                        d_item = item[0:len(item) - 2].decode("utf-8")
                    except UnicodeDecodeError:
                        d_item = '0 0 0'
                    aux_data.append(list(map(int, d_item.split(' '))))
                aux_data = np.asarray(aux_data)
                sio.savemat(os.path.join(aux_savepath, filename+'_b.mat'), {'aux': aux_data})


def configure_cam(cam, camnum):
    result = True
    verbose = 0
    if camnum == 0 and cfg['verbose']:
        verbose = 1

    if verbose:
        print('*** CONFIGURING CAMERA(S) ***\n')
    try:
        nodemap = cam.GetNodeMap()
        # Ensure trigger mode off
        # The trigger must be disabled in order to configure whether the source
        # is software or hardware.
        node_trigger_mode = PySpin.CEnumerationPtr(nodemap.GetNode('TriggerMode'))
        if not PySpin.IsAvailable(node_trigger_mode) or not PySpin.IsReadable(node_trigger_mode):
            print('Unable to disable trigger mode 129 (node retrieval). Aborting...')
            return False

        node_trigger_mode_off = node_trigger_mode.GetEntryByName('Off')
        if not PySpin.IsAvailable(node_trigger_mode_off) or not PySpin.IsReadable(node_trigger_mode_off):
            print('Unable to disable trigger mode (enum entry retrieval). Aborting...')
            return False

        node_trigger_mode.SetIntValue(node_trigger_mode_off.GetValue())

        node_trigger_source = PySpin.CEnumerationPtr(nodemap.GetNode('TriggerSource'))
        if not PySpin.IsAvailable(node_trigger_source) or not PySpin.IsWritable(node_trigger_source):
            print('Unable to get trigger source 163 (node retrieval). Aborting...')
            return False

        # Set primary camera trigger source to line0 (hardware trigger)
        if framerate == 'hardware':
            node_trigger_source_set = node_trigger_source.GetEntryByName('Line0')
            if verbose:
                print('Trigger source set to hardware...\n')
        else:
            node_trigger_source_set = node_trigger_source.GetEntryByName('Software')
            if verbose:
                print('Trigger source set to software, framerate = %i...\n' % framerate)

        if not PySpin.IsAvailable(node_trigger_source_set) or not PySpin.IsReadable(
                node_trigger_source_set):
            print('Unable to set trigger source (enum entry retrieval). Aborting...')
            return False

        node_trigger_source.SetIntValue(node_trigger_source_set.GetValue())
        node_trigger_mode_on = node_trigger_mode.GetEntryByName('On')

        if not PySpin.IsAvailable(node_trigger_mode_on) or not PySpin.IsReadable(node_trigger_mode_on):
            print('Unable to enable trigger mode (enum entry retrieval). Aborting...')
            return False

        node_trigger_mode.SetIntValue(node_trigger_mode_on.GetValue())

        # Set acquisition mode to continuous
        node_acquisition_mode = PySpin.CEnumerationPtr(nodemap.GetNode('AcquisitionMode'))
        if not PySpin.IsAvailable(node_acquisition_mode) or not PySpin.IsWritable(node_acquisition_mode):
            print('Unable to set acquisition mode to continuous (enum retrieval). Aborting...')
            return False

        # Retrieve entry node from enumeration node
        node_acquisition_mode_continuous = node_acquisition_mode.GetEntryByName('Continuous')
        if not PySpin.IsAvailable(node_acquisition_mode_continuous) or not PySpin.IsReadable(
                node_acquisition_mode_continuous):
            print('Unable to set acquisition mode to continuous (entry retrieval). Aborting...')
            return False

        # Retrieve integer value from entry node
        acquisition_mode_continuous = node_acquisition_mode_continuous.GetValue()

        # Set integer value from entry node as new value of enumeration node
        node_acquisition_mode.SetIntValue(acquisition_mode_continuous)

        # Retrieve Stream Parameters device nodemap
        s_node_map = cam.GetTLStreamNodeMap()

        # Retrieve Buffer Handling Mode Information
        handling_mode = PySpin.CEnumerationPtr(s_node_map.GetNode('StreamBufferHandlingMode'))
        if not PySpin.IsAvailable(handling_mode) or not PySpin.IsWritable(handling_mode):
            print('Unable to set Buffer Handling mode (node retrieval). Aborting...\n')
            return False

        handling_mode_entry = PySpin.CEnumEntryPtr(handling_mode.GetCurrentEntry())
        if not PySpin.IsAvailable(handling_mode_entry) or not PySpin.IsReadable(handling_mode_entry):
            print('Unable to set Buffer Handling mode (Entry retrieval). Aborting...\n')
            return False

        # Set stream buffer Count Mode to manual
        stream_buffer_count_mode = PySpin.CEnumerationPtr(s_node_map.GetNode('StreamBufferCountMode'))
        if not PySpin.IsAvailable(stream_buffer_count_mode) or not PySpin.IsWritable(stream_buffer_count_mode):
            print('Unable to set Buffer Count Mode (node retrieval). Aborting...\n')
            return False

        stream_buffer_count_mode_manual = PySpin.CEnumEntryPtr(stream_buffer_count_mode.GetEntryByName('Manual'))
        if not PySpin.IsAvailable(stream_buffer_count_mode_manual) or not PySpin.IsReadable(
                stream_buffer_count_mode_manual):
            print('Unable to set Buffer Count Mode entry (Entry retrieval). Aborting...\n')
            return False

        stream_buffer_count_mode.SetIntValue(stream_buffer_count_mode_manual.GetValue())

        # Retrieve and modify Stream Buffer Count
        buffer_count = PySpin.CIntegerPtr(s_node_map.GetNode('StreamBufferCountManual'))
        if not PySpin.IsAvailable(buffer_count) or not PySpin.IsWritable(buffer_count):
            print('Unable to set Buffer Count (Integer node retrieval). Aborting...\n')
            return False

        # Set new buffer value
        buffer_count.SetValue(1000)

        # # Retrieve and modify resolution
        # node_width = PySpin.CIntegerPtr(nodemap.GetNode('Width'))
        # if PySpin.IsAvailable(node_width) and PySpin.IsWritable(node_width):
        #     width_to_set = int(1440/bin_val)
        #     node_width.SetValue(width_to_set)
        #     if verbose:
        #         print('Width set to %i...' % node_width.GetValue())
        # else:
        #     if verbose:
        #         print('Width not available, width is %i...' % node_width.GetValue())

        # node_height = PySpin.CIntegerPtr(nodemap.GetNode('Height'))
        # if PySpin.IsAvailable(node_height) and PySpin.IsWritable(node_height):
        #     height_to_set = int(1080/bin_val)
        #     node_height.SetValue(height_to_set)
        #     if verbose:
        #         print('Height set to %i...' % node_height.GetValue())
        # else:
        #     if verbose:
        #         print('Width not available, height is %i...' % node_height.GetValue())

        # Access trigger overlap info
        node_trigger_overlap = PySpin.CEnumerationPtr(nodemap.GetNode('TriggerOverlap'))
        if not PySpin.IsAvailable(node_trigger_overlap) or not PySpin.IsWritable(node_trigger_overlap):
            print('Unable to set trigger overlap to "Read Out". Aborting...')
            return False

        # Retrieve enumeration for trigger overlap Read Out
        if framerate == 'hardware':
            node_trigger_overlap_ro = node_trigger_overlap.GetEntryByName('ReadOut')
        else:
            node_trigger_overlap_ro = node_trigger_overlap.GetEntryByName('Off')

        if not PySpin.IsAvailable(node_trigger_overlap_ro) or not PySpin.IsReadable(
                node_trigger_overlap_ro):
            print('Unable to set trigger overlap (entry retrieval). Aborting...')
            return False

        # Retrieve integer value from enumeration
        trigger_overlap_ro = node_trigger_overlap_ro.GetValue()

        # Set trigger overlap using retrieved integer from enumeration
        node_trigger_overlap.SetIntValue(trigger_overlap_ro)

        # Access exposure auto info
        node_exposure_auto = PySpin.CEnumerationPtr(nodemap.GetNode('ExposureAuto'))
        if not PySpin.IsAvailable(node_exposure_auto) or not PySpin.IsWritable(node_exposure_auto):
            print('Unable to get exposure auto. Aborting...')
            return False

        # Retrieve enumeration for trigger overlap Read Out
        node_exposure_auto_off = node_exposure_auto.GetEntryByName('Off')
        if not PySpin.IsAvailable(node_exposure_auto_off) or not PySpin.IsReadable(
                node_exposure_auto_off):
            print('Unable to get exposure auto "Off" (entry retrieval). Aborting...')
            return False

        # Set exposure auto to off
        node_exposure_auto.SetIntValue(node_exposure_auto_off.GetValue())

        # Access exposure info
        node_exposure_time = PySpin.CFloatPtr(nodemap.GetNode('ExposureTime'))
        if not PySpin.IsAvailable(node_exposure_time) or not PySpin.IsWritable(node_exposure_time):
            print('Unable to get exposure time. Aborting...')
            return False

        # Set exposure float value
        node_exposure_time.SetValue(exp_time * 1000000)
        if verbose:
            print('Exposure time set to ' + str(exp_time*1000) + 'ms...')

    except PySpin.SpinnakerException as ex:
        print('Error (237): %s' % ex)
        return False

    return result


def config_and_acquire(camlist):
    thread = []
    for i, cam in enumerate(camlist):
        cam.Init()
        configure_cam(cam, i)
        nodemap = cam.GetNodeMap()
        cam.BeginAcquisition()
        thread.append(ThreadCapture(cam, i, nodemap))
        thread[i].start()

    if framerate == 'hardware':
        print('*** WAITING FOR FIRST TRIGGER... ***\n')

    for t in thread:
        t.join()

    for i, cam in enumerate(camlist):
        reset_trigger(cam)
        cam.DeInit()

# Config camera params, but don't begin acquisition
def config_and_return(camlist):
    for i, cam in enumerate(camlist):
        cam.Init()
        configure_cam(cam, i)

    for i, cam in enumerate(camlist):
        reset_trigger(cam)
        cam.DeInit()


def reset_trigger(cam):
    nodemap = cam.GetNodeMap()
    try:
        result = True
        node_trigger_mode = PySpin.CEnumerationPtr(nodemap.GetNode('TriggerMode'))
        if not PySpin.IsAvailable(node_trigger_mode) or not PySpin.IsReadable(node_trigger_mode):
            print('Unable to disable trigger mode 630 (node retrieval). Aborting...')
            return False

        node_trigger_mode_off = node_trigger_mode.GetEntryByName('Off')
        if not PySpin.IsAvailable(node_trigger_mode_off) or not PySpin.IsReadable(node_trigger_mode_off):
            print('Unable to disable trigger mode (enum entry retrieval). Aborting...')
            return False
        
        node_trigger_mode.SetIntValue(node_trigger_mode_off.GetValue())

    except PySpin.SpinnakerException as ex:
        print('Error (663): %s' % ex)
        result = False
        
    return result


def main():
    # Check write permissions
    try:
        test_file = open('test.txt', 'w+')
    except IOError:
        print('Unable to write to current directory. Please check permissions.')
        return False

    test_file.close()
    os.remove(test_file.name)
    result = True
    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()
    num_cameras = cam_list.GetSize()

    print('Number of cameras detected: %d' % num_cameras)

    if num_cameras == 0:
        cam_list.Clear()
        system.ReleaseInstance()
        print('Not enough cameras! Goodbye.')
        return False
    elif num_cameras > 0 and int(sys.argv[1]) == 1:
        config_and_acquire(cam_list)
    else:
        config_and_return(cam_list)

    # Clear cameras and release system instance
    cam_list.Clear()
    system.ReleaseInstance()

    # Close serial connection
    if ser_avail:
        ser.close()

    # Save DAQ data
    if DAQ_online and int(sys.argv[1]) == 1:
        data = ai_task.read(number_of_samples_per_channel=DAQ_ns)
        DAQdata = np.asarray(data)

        # Create plot of DAQ data
        t = np.linspace(0, .5, num=fs//2)
        plt.plot(t, np.transpose(DAQdata[:, 0:fs//2]))
        plt.savefig(aux_savepath+filename+'_DAQ.png')

        # Write DAQ data to .mat file
        sio.savemat(aux_savepath+filename+'_DAQ.mat', {'DAQdata': DAQdata})

        # Close DAQ tasks
        ai_task.close()
        ao_task.close()
        print('DAQ data saved. \n')

    print('DONE')
    time.sleep(.5)
    print('Goodbye :)')
    time.sleep(2)
    return result


if __name__ == '__main__':

    main()
