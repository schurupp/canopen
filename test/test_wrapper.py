import unittest
import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from enum import Enum
import canopen
from wrapper.node import DeclarativeNode, ObjectDef, BitField, RPDOMap

class Mode(Enum):
    INIT = 0
    AUTO = 1
    MANUAL = 2

class MockNode(DeclarativeNode):
    status = ObjectDef("Status", 0x2000, sub=0, type="UNSIGNED16")
    
    error_flag = BitField(target=status, bits=[0], type=bool)
    mode = BitField(target=status, bits=[1, 2], type=Mode, enum_class=Mode)
    counter = BitField(target=status, bits=[3, 4, 5, 6, 7], type=int)

    rpdo1 = RPDOMap(0x201, 254, payload=[status])


class TestDeclarativeWrapper(unittest.TestCase):
    def setUp(self):
        self.network = canopen.Network()
        self.network.connect(interface='virtual', channel='test_ch1', bitrate=500000, receive_own_messages=True)
        
        # Zero-Config Boot Sequence!
        self.node = MockNode(node_id=1)
        self.network.add_node(self.node)
        self.node.status = 0

    def tearDown(self):
        self.network.disconnect()

    def test_bitfields(self):
        self.node.error_flag = True
        self.assertEqual(self.node.error_flag, True)
        self.assertEqual(self.node.status, 1)

        self.node.mode = Mode.AUTO
        self.assertEqual(self.node.status, 1 | (1 << 1))

        self.node.counter = 5
        self.assertEqual(self.node.counter, 5)

    def test_rpdo_store_immediate(self):
        # Emit an RPDO via network
        # 0x2000 is UN16 -> 2 bytes. Let's send 0x0B 0x00 -> 11 -> Error = 1, Mode = 1 (Auto), Counter = 1
        self.network.send_message(0x201, b'\x0B\x00')
        time.sleep(0.1)

        # Check if the node's local storage inherited the 11 mathematically!
        self.assertEqual(self.node.status, 11)
        self.assertEqual(self.node.error_flag, True)
        self.assertEqual(self.node.mode, Mode.AUTO)
        self.assertEqual(self.node.counter, 1)

if __name__ == '__main__':
    unittest.main()
