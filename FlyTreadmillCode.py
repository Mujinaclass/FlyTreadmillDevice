
#   Pin layout
# RasPi  | ADNS3080
#  3V3   |   3.3V
#  GND   |   GND
# SPIMOSI|   MOSI
# SPIMISO|   MISO
# SPISCLK|   SCLK
# SPICE0 |   NCS
# GPIO25 |   RST

# RasPi  | DVR8833 | Motor | Power | Condenser (100uF)
# GPIO17 |   DIR   |       |       |
# GPIO27 |   STEP  |       |       |
# GPIO22 |   SLP   |       |       |
# GPIO23 |   M1    |       |       |
# GPIO24 |   M0    |       |       |
#  GND   |   GND   |       |       |
#        |   A1    | Blacl |       |
#        |   A2    | Green |       |
#        |   B1    | Red   |       |
#        |   A2    | Blue  |       |
#        |   VMOT  |       |   +   |      +
#        |   GND   |       |  GND  |      -

# Microstep Resolution  |   M0   |  M1
#         Full          |  LOW   | HIGH
#         Half          |  HIGH  | LOW
#         1/4           |Floating| LOW
#         1/8           |  LOW   | HIGH
#         1/16          |  HIGH  | HIGH
#         1/32          |Floating| HIGH
# *Leaving selection pins disconnected results "Floating".

import pigpio
import time
import numpy
import PIL.Image, PIL.ImageTk
from Tkinter import *
from threading import Timer

##### Set parameters for ADNS3080 #####
RESET_PIN = 25                                   #GPIO25 for reset ADNS3080
SPI_CHANNEL = 0                                  # GPIO8(CE0) if choose CE1, set 1
SPI_MODE = 3                                     #SPI mode as two bit pattern of clock polarity and phase
                                                 #[CPOL|CPHA] > [0|0] = 0 (mode 0), [0|1] = 1 (mode 1), [1|0] = 2 (mode 2), [1|1] = 3 (mode 3) 
SPI_MAX_SPEED = 500000
SPI_OPEN = False

# Register Map for the ADNS3080 OpticalFlow Sensor
ADNS3080_PRODUCT_ID = 0x00
ADNS3080_CONFIGURATION_BITS = 0x0a
ADNS3080_MOTION_BURST = 0x50
ADNS3080_FRAME_CAPTURE = 0x13
ADNS3080_PRODUCT_ID_VALUE = 0x17

# Pixel size of ADNS3080
ADNS3080_PIXELS_X = 30
ADNS3080_PIXELS_Y = 30

DATA_FOR_CAPTURE_IMAGE = [0xff,ADNS3080_FRAME_CAPTURE]*899 + [0xff]     # make [0xff (dummy 1), Address, 0xff (2), Address, ..., Adreess, 0xff (900)]
##### End defines of ADNS3080 parameters#####

##### Set parameters for Stepping motor control #####
MOTOR_STEP_PIN = 27                # GPIO27: STEP command
MOTOR_DIR_PIN = 17                 # GPIO17: Direction CW:HIGH  CCW:LOW
MOTOR_SLEEP_PIN = 22               # GPIO22: Sleep mode
MOTOR_M0_PIN = 24
MOTOR_M1_PIN = 23
MOTOR_STEP_ANGLE = 1.8             # Motor full step angle: 1.8 deg./step
MOTOR_SPEED_LIMIT = 5              # [rps] Stepping Motor limit is 5 rps.
MOTOR_STEPRESMODES_DICT = {1: (0,0), 2: (1,0), 4: (0,0), 8: (0,1), 16: (1,1), 32: (0,1)} # Resolution: (M0 pin, M1 pin)

motor_step_resolution = 1

SCREW_STANDARD = 'M5'
SCREW_LEAD = 0.8                   # [mm] Lead = pitch

SYRINGE_AREA = 165.13              # [mm^2]

AVAIRABLE_PWM_FREQ_DICT = { 1: (50,100,200,250,400,500,800,1000,1250,1600,2000,2500,4000,5000,8000,10000,20000,40000), # Sample rate [us]: (Hertz)
                            2: (25, 50,100,125,200,250,400, 500, 625, 800,1000,1250,2000,2500,4000, 5000,10000,20000),
                            4: (13, 25, 50, 63,100,125,200, 250, 313, 400, 500, 625,1000,1250,2000, 2500, 5000,10000),
                            5: (10, 20, 40, 50, 80,100,160, 200, 250, 320, 400, 500, 800,1000,1600, 2000, 4000, 8000),
                            8: ( 6, 13, 25, 31, 50, 63,100, 125, 156, 200, 250, 313, 500, 625,1000, 1250, 2500, 5000),
                           10: ( 5, 10, 20, 25, 40, 50, 80, 100, 125, 160, 200, 250, 400, 500, 800, 1000, 2000, 4000)}

PWM_DUTYCYCLE = (0, 128)           # PWM (STOP, 1/2 on)
PWM_SAMPLE_RATE = 5                # Default is 5 us. If you want to change sample rate, type "sudo pigpiod -s <sample rate>" in comand line, when you start the pigpio deamon. 


AVAIRABLE_FLOW_RATE_DICT = {}      # {Flow rate [ml/s]: PWM frequency [Hz]}
for freq in range(len(AVAIRABLE_PWM_FREQ_DICT[PWM_SAMPLE_RATE])):
    RPM = (MOTOR_STEP_ANGLE / motor_step_resolution * AVAIRABLE_PWM_FREQ_DICT[PWM_SAMPLE_RATE][freq]) / 360  # [rotation / sec]
    flow_rate = RPM * SCREW_LEAD * SYRINGE_AREA * 10**-3 # [ml / sec]
    if RPM <= MOTOR_SPEED_LIMIT:
        AVAIRABLE_FLOW_RATE_DICT[flow_rate] = AVAIRABLE_PWM_FREQ_DICT[PWM_SAMPLE_RATE][freq]
    else:
        thing = None               # do nothing
##### End defines of Stepping motor control parameters#####



class GUI():
    grid_size = 10
    pixelValue = [0 for i in xrange(ADNS3080_PIXELS_X*ADNS3080_PIXELS_Y)]
    position_X = 0
    position_Y = 0
    capture_image = True
    position_gap = (grid_size*ADNS3080_PIXELS_X) / 2

    motor_direction = 1                # direcion = 0 (CCW) or 1 (CW)
    motor_moving_dulation = 1          # [sec]

    def __init__(self, master):
        master.title("ADNS3080 Capture Image")        # set main window's title
        master.geometry("600x600")                    # set window's size
        
        self.canvas_for_Image = Canvas(master, width = self.grid_size*ADNS3080_PIXELS_X, height = self.grid_size*ADNS3080_PIXELS_Y)
        self.canvas_for_Image.place(x=0,y=0)

        self.canvas_for_Plot = Canvas(master, width = self.grid_size*ADNS3080_PIXELS_X, height = self.grid_size*ADNS3080_PIXELS_Y)
        self.canvas_for_Plot.place(x=self.grid_size*ADNS3080_PIXELS_X,y=0)
        self.canvas_for_Plot.create_rectangle(0, 0, self.grid_size*ADNS3080_PIXELS_X, self.grid_size*ADNS3080_PIXELS_Y, width=0, fill="white")
        # make grid on plot area
        for i in xrange(6):
            for j in xrange(6):
                self.canvas_for_Plot.create_rectangle(j*50+1, i*50+1, (j+1)*50-1, (i+1)*50-1, width=0, fill="lightgray")
        self.init_data = self.canvas_for_Plot.create_oval(self.position_X - self.grid_size/2 + self.position_gap, self.position_Y - self.grid_size/2 + self.position_gap,\
                                                         self.position_X + self.grid_size/2 + self.position_gap, self.position_Y + self.grid_size/2 + self.position_gap, fill = 'blue')
        self.old_data = self.init_data
        
        
        self.button_exit = Button(master, text="STOP", width = 15, command = self.endProgram)
        self.button_exit.place(x=0,y=self.grid_size*ADNS3080_PIXELS_Y+self.grid_size)

        self.button_change_status = Button(master, text="Change Mode", width = 15, command = self.change_status)
        self.button_change_status.place(x=self.grid_size*ADNS3080_PIXELS_X/2,y=self.grid_size*ADNS3080_PIXELS_Y+self.grid_size)

        self.CamStatus = StringVar()
        self.Camlabel = Label(master,textvariable=self.CamStatus,bg='green',font=("Helvetica",12))
        self.Camlabel.place(x=0,y=0)
        self.CamStatus.set("Image capture mode: ON")

        self.PlotStatus = StringVar()
        self.Plotlabel = Label(master,textvariable=self.PlotStatus,bg='red',font=("Helvetica",12))
        self.Plotlabel.place(x=self.grid_size*ADNS3080_PIXELS_X,y=0)
        self.PlotStatus.set("Move tracking mode: OFF")

        self.button_Motor = Button(master, text="MOTOR START", width = 15, command = lambda : self.moveSteppingMotor(self.motor_direction,self.motor_moving_dulation))   # command fnc is nomally called without argument. That is why use lambda.
        #self.button_Motor.bind("<Button-1>",self.moveSteppingMotor(self.motor_direction,self.motor_moving_dulation))
        self.button_Motor.place(x=0,y=self.grid_size*ADNS3080_PIXELS_Y+self.grid_size*5)

        self.read_loop()                              # start attempts to read image from ADNS3080 via SPI

    def moveSteppingMotor(self,direc,dulat):
        try:
            #self.timer.cancel()
            pi.write(MOTOR_DIR_PIN,direc)
            pi.set_PWM_dutycycle(MOTOR_STEP_PIN, PWM_DUTYCYCLE[1])  # PWM on
            time.sleep(dulat)
            pi.set_PWM_dutycycle(MOTOR_STEP_PIN, PWM_DUTYCYCLE[0])  # PWM off
            print("debug")
        except:
            thing = None

    def plotData(self):
        self.canvas_for_Plot.delete(self.old_data)
        del(self.old_data)
        self.new_data = self.canvas_for_Plot.create_oval(self.position_X - self.grid_size/2 + self.position_gap, self.position_Y - self.grid_size/2 + self.position_gap,\
                                                         self.position_X + self.grid_size/2 + self.position_gap, self.position_Y + self.grid_size/2 + self.position_gap, fill = 'blue')
        self.old_data = self.new_data

    def __del__(self):
        self.endProgram()

    def endProgram(self):
        global SPI_OPEN
        try:
            self.timer.cancel()
            SPI_OPEN = False
            self.Camlabel.config(background="red")
            self.Plotlabel.config(background="red")
            self.CamStatus.set("Image capture mode: OFF")
            self.PlotStatus.set("Move tracking mode: OFF")
        except:
            print("failed to exit program")

    def change_status(self):
        global SPI_OPEN
        self.timer.cancel()
        if self.capture_image == False:
            self.capture_image = True
            self.Camlabel.config(background="green")
            self.CamStatus.set("Image capture mode: ON")
            self.Plotlabel.config(background="red")
            self.PlotStatus.set("Move tracking mode: OFF")
        else:
            self.capture_image = False
            self.Camlabel.config(background="red")
            self.CamStatus.set("Image capture mode: OFF")
            self.Plotlabel.config(background="green")
            self.PlotStatus.set("Move tracking mode: ON")
        time.sleep(10e-5)
        resetADNS3080()                               # must reset to change mode in ADNS3080 to get dx dy
        checkConnect()
        configuration()

    def read_loop(self):
        try:
            self.timer.cancel()
        except:
            thing = None # do nothing

        if SPI_OPEN == True:
            if self.capture_image == True:
                self.printPixelData()
            else:
                self.updateDxDy()

            self.timer = Timer(0.0, self.read_loop)
            self.timer.start()
        else:
            thing = None # do nothing
            
    def printPixelData(self):
        spiWrite(ADNS3080_FRAME_CAPTURE,[0x83])
        time.sleep(1510e-6)
        regValue = spiRead(ADNS3080_FRAME_CAPTURE,DATA_FOR_CAPTURE_IMAGE)
        regValue = numpy.asarray(regValue[::2]) & 0x3f    # Only lower 6bits have data
        regValue = regValue.reshape(30,30) * 4
        
        CapImage = PIL.Image.fromarray(regValue)          # convert numpyarray to PIL image object
        CapImage = CapImage.resize((self.grid_size*ADNS3080_PIXELS_X,self.grid_size*ADNS3080_PIXELS_Y))  # resize Image (30x30 -> 300x300)
        
        self.tkpi = PIL.ImageTk.PhotoImage(CapImage)      # convert PIL image object to ImageTk object
 
        self.canvas_for_Image.create_image(0,0,anchor=NW,image=self.tkpi)
        self.hoge = self.tkpi                             # this line should be needed to prevent blink. I don't know why this helps.

    def updateDxDy(self):
        buf = [0 for i in xrange(4)]
        buf = spiRead(ADNS3080_MOTION_BURST,buf)
        motion = buf[0]
        if  (motion & 0x10):
            print("ADNS-3080 overflow")
        elif (motion & 0x80):
            dx = buf[1] if(buf[1]<0x80) else (buf[1]-0xFF)
            dy = buf[2] if(buf[2]<0x80) else (buf[2]-0xFF)
            surfaceQuality = buf[3]

            self.position_X += dx
            self.position_Y += dy

            print('x:{0},dx:{1}  y:{2},dy:{3}  surfaceQuality:{4}'.format(self.position_X,dx,self.position_Y,dy,surfaceQuality))
        self.plotData()
        time.sleep(0.01)
#end class GUI()


def spiSettings(spi_channel,spi_speed,spi_mode):
    global pi, spi, SPI_OPEN
    try:
        pi = pigpio.pi()
        spi = pi.spi_open(spi_channel,spi_speed,spi_mode)   # open SPI device on <SPI_CHANNEL> in <SPI_MODE> at <SPI_MAX_SPEED> bits per sec
        SPI_OPEN = True
    except:
        print("Could not open SPI")
#end def spiSettings()

def resetADNS3080():
    pi.set_mode(RESET_PIN, pigpio.OUTPUT)
    pi.write(RESET_PIN, 1)
    time.sleep(10e-6)
    pi.write(RESET_PIN, 0)
    time.sleep(500e-6)
#end def resetADNS3080()

def checkConnect():
    resp = spiRead(ADNS3080_PRODUCT_ID,[0xff])              # I don'n know why I should try double.
    resp = spiRead(ADNS3080_PRODUCT_ID,[0xff])
    product_ID = resp[0]
    if product_ID == ADNS3080_PRODUCT_ID_VALUE:
        print("ADNS-3080 found. Product ID: 0x" '%x' %product_ID)
    else:
        print("Could not found ADNS-3080: 0x" '%x' %product_ID)
#end def checkConnect()

def configuration():
    config = spiRead(ADNS3080_CONFIGURATION_BITS,[0xff])
    spiWrite(ADNS3080_CONFIGURATION_BITS,[config[0] | 0x10])  #Set resolution to 1600 counts per inch
    config = spiRead(ADNS3080_CONFIGURATION_BITS,[0xff])
    if (config[0] & 0x19):
        print("Resolution in counts per inch = 1600")
    else:
        print("Resolution in counts per inch = 400")
#end def configuration()

def spiRead(reg,data):                                        #"data" must be list
    length = len(data)
    to_send = [reg]
    to_send += data
    resp = pi.spi_xfer(spi,to_send)                           # resp is tuple (count, rx_data (bytearray))
    return list(resp[1])[1:length+1]
#end def spiRead()

def spiWrite(reg,data):
    to_send = [reg | 0x80]
    to_send += data
    pi.spi_write(spi,to_send)
#end def spiWrite()


def motorSettings(stepResolution):
    global pi, motor_step_resolution
    pi.set_mode(MOTOR_STEP_PIN,pigpio.OUTPUT)
    pi.set_mode(MOTOR_DIR_PIN,pigpio.OUTPUT)
    pi.set_mode(MOTOR_SLEEP_PIN,pigpio.OUTPUT)
    pi.set_mode(MOTOR_M0_PIN,pigpio.OUTPUT)
    pi.set_mode(MOTOR_M1_PIN,pigpio.OUTPUT)

    pi.write(MOTOR_SLEEP_PIN,1)
    time.sleep(1e-3)               # Wake-up time. Need to set over 1msec

    if stepResolution in MOTOR_STEPRESMODES_DICT:
        pi.write(MOTOR_M0_PIN,MOTOR_STEPRESMODES_DICT[stepResolution][0])
        pi.write(MOTOR_M1_PIN,MOTOR_STEPRESMODES_DICT[stepResolution][1])
    else:
        motor_step_resolution = 1  # Set step resolution to Full-step mode
        pi.write(MOTOR_M0_PIN,0)
        pi.write(MOTOR_M1_PIN,0)
        print("Invalid value. Step resolution should be within the following values.")
        print("Full > 1, Half > 2, 1/4 > 4, 1/8 > 8, 1/16 > 16, 1/32 > 32")
        print("Step resolution is now Full-step (default).")

    if stepResolution == 4 or stepResolution == 32:
        print("Please disconnect M0 pin: GPIO" '%d' %MOTOR_M0_PIN)
#end def motorSettings()

def motorFreqSetting():
    pi.set_PWM_frequency(MOTOR_STEP_PIN, AVAIRABLE_PWM_FREQ_DICT[PWM_SAMPLE_RATE][12])
    pi.set_PWM_dutycycle(MOTOR_STEP_PIN, PWM_DUTYCYCLE[0])
#end def motorFreqSetting()


## ADNS3080 Settings ##
spiSettings(SPI_CHANNEL,SPI_MAX_SPEED,SPI_MODE)
resetADNS3080()
checkConnect()
configuration()

## Motor Settings ##
motorSettings(motor_step_resolution)
motorFreqSetting()

## main loop ##
root = Tk()

gui = GUI(root)

print("entering main loop!")

root.mainloop()

gui.endProgram()

pi.spi_close(spi)
pi.write(MOTOR_SLEEP_PIN,0)

print("existing")
