#!/usr/bin/env python2
## 
## vita-petite-mort-manual.py -- run brom payloads
##
## Copyright (C) 2016 molecule
##
## This software may be modified and distributed under the terms
## of the MIT license.  See the LICENSE file for details.
## 
from __future__ import print_function, division

import time
import logging
import os
import csv
from enum import IntEnum

import chipwhisperer as cw
import sys
from chipwhisperer.capture.scopes.cwhardware.ChipWhispererExtra import CWExtraSettings
from chipwhisperer.capture.targets.mmccapture_readers._base import MMCPacket
from chipwhisperer.capture.targets.MMCCapture import MMCCapture as cwtarget
from chipwhisperer.common.utils import pluginmanager
from chipwhisperer.capture.targets.simpleserial_readers.cwlite import SimpleSerial_ChipWhispererLite

# Params

CW_SYSCLK_FREQ = 96000000
VITA_CLK_FREQ = 12000000
MIN_OFFSET = 0
MAX_OFFSET = 3500
MIN_WIDTH = 1
MAX_WIDTH = 200
VITA_UART0_BAUD = 28985
TIME_RESET_HOLD = 0
TIME_POWER_HOLD = 5
OFFSET_STEP = 10
WIDTH_STEP = 10
GLITCH_FIND_TIMEOUT = 10
WAIT_FOR_UART_TIMEOUT = 50
PAYLOAD_TIMEOUT = 1000
VERBOSE = 1

# From https://gist.github.com/sbz/1080258
def hexdump(src, offset, length=16):
    FILTER = ''.join([(len(repr(chr(x))) == 3) and chr(x) or '.' for x in range(256)])
    lines = []
    for c in xrange(0, len(src), length):
        chars = src[c:c+length]
        hex = ' '.join(["%02x" % ord(x) for x in chars])
        printable = ''.join(["%s" % ((ord(x) <= 127 and FILTER[ord(x)]) or '.') for x in chars])
        lines.append("%08x  %-*s  %s\n" % (offset + c, length*3, hex, printable))
    return ''.join(lines)

logging.basicConfig(level=logging.WARN)
scope = cw.scope()
target = cw.target(scope, cwtarget)

# setup parameters needed for glitch the stm32f
scope.glitch.clk_src = 'clkgen'

scope.clock.clkgen_freq = VITA_CLK_FREQ
scope.io.tio1 = "serial_tx"
scope.io.tio2 = "serial_rx"

# setup MMC trigger to look for READ_SINGLE_BLOCK of 0x0 response

mmctrigger = scope.mmcTrigger
mmctrigger.setMatchCmd(True)
mmctrigger.setCmdIndex(MMCPacket.Cmd.READ_SINGLE_BLOCK.value)
mmctrigger.setDirection(2)
mmctrigger.setDataCompareOp(1)
mmctrigger.setTriggerData('0x842A')
mmctrigger.setTriggerNext(True)

# get MMC output
mmc = target.mmc

# get serial console
ser_cons = pluginmanager.getPluginsInDictFromPackage("chipwhisperer.capture.targets.simpleserial_readers", True, False)
ser = ser_cons[SimpleSerial_ChipWhispererLite._name]
ser.con(scope)
ser.setBaud(VITA_UART0_BAUD)

# format output table
headers = ['num packets', 'width', 'offset', 'success']
#glitch_display = GlitchResultsDisplay(headers)

# set glitch parameters
# trigger glitches with external trigger
scope.glitch.trigger_src = 'manual'
scope.glitch.output = 'enable_only'
scope.io.hs2 = 'clkgen'

# enable trigger
#scope.advancedSettings.cwEXTRA.setTriggerModule(CWExtraSettings.MODULE_MMCTRIGGER)
scope.advancedSettings.cwEXTRA.setTargetGlitchOut('A', True)

# init
target.init()

print('Starting glitch...')
success = False
while not success:
    for offset in xrange(MIN_OFFSET, MAX_OFFSET+1, OFFSET_STEP):
        # set offset from trigger
        #scope.glitch.ext_offset = offset
        for width in xrange(MIN_WIDTH, MAX_WIDTH+1, WIDTH_STEP):
            print('trying offset {}, width {}'.format(offset, width))

            scope.glitch.repeat = 85
            #scope.glitch.repeat = 1
            # flush the buffer
            time.sleep(TIME_RESET_HOLD)

            # power on and hold reset
            print('Waiting for Vita to power on...')
            scope.io.pdid = 'low'
            while mmc.count() == 0:
                pass
            scope.io.pdid = 'disabled'

            timeout = GLITCH_FIND_TIMEOUT
            # wait for target to finish

            last_cnt = 0
            while mmc.count() > 0:
                pkt = mmc.read()
                last_cnt = pkt.num
                if VERBOSE:
                    print(str(pkt))
            ser.read(ser.inWaiting())

            timestamp = 0
            while timeout > 0:
                while mmc.count() > 0:
                    timeout = GLITCH_FIND_TIMEOUT
                    pkt = mmc.read()
                    if pkt.num < last_cnt:
                        timestamp = ((pkt.num + 0x10000 - last_cnt) * 0x100 * 1000.0) / CW_SYSCLK_FREQ
                    else:
                        timestamp = ((pkt.num - last_cnt) * 0x100 * 1000.0) / CW_SYSCLK_FREQ
                    last_cnt = pkt.num
                    print('[{:10.5f}ms] {}'.format(timestamp, str(pkt)))
                    if pkt.is_req:
                        if pkt.cmd == MMCPacket.Cmd.READ_SINGLE_BLOCK and pkt.content == 0x842A:
                            timeout = -1
                            break
                else:
                    time.sleep(0.2)
                    timeout -= 1

            if timeout < 0:
                print('before trigger')
                time.sleep(offset * 1.0 / 1000.0)
                for i in range(width):
                    scope.glitch.manualTrigger()
                print('after trigger')
                print('Waiting for UART data...')
                timeout = WAIT_FOR_UART_TIMEOUT
                count = 0
                while timeout > 0:
                    count = ser.inWaiting()
                    if count > 0:
                        break
                    time.sleep(0.1)
                    timeout -= 1
                #if count > 0:
                #    print('SUCCESS: ' + str(data))
                #    print('SUCCESS: ' + hexdump(ser.read(ser.inWaiting(), 0), 0), end="")

                success = (count > 0)
                if success:
                    print(str(timeout))

            # for table display purposes
            data = [offset, width]
            print(data)
            #glitch_display.add_data(data)

            if success:
                break
        if success:
            break

# the rest of the data is available with the outputs, widths, and offsets lists
#glitch_display.display_table()
print('Glitch successful, waiting for UART data...')

f = None
if len(sys.argv) > 1:
    path = sys.argv[1]
    print('Dumping to {}'.format(path))
    f = open(path, "wb")

timeout = PAYLOAD_TIMEOUT
offset = 0
queue = []
while timeout > 0:
    count = ser.inWaiting()
    while count > 0:
        timeout = PAYLOAD_TIMEOUT
        dat = ser.read(count, 0)
        queue.extend(dat)
        if f:
            f.write(dat)
            f.flush()
        if (not f or VERBOSE) and len(queue) >= 16:
            print(hexdump(queue[0:16], offset), end="")
            queue = queue[16:]
            offset += 16
        count = ser.inWaiting()
    else:
        time.sleep(0.1)
        timeout -= 1
    while VERBOSE and mmc.count() > 0:
        pkt = mmc.read()
        print(str(pkt))

if f:
    f.close()

print('Timed out.')
