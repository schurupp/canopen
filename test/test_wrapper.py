import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from enum import Enum
import canopen
from wrapper.node import CustomNode, BitFieldDef

class Mode(Enum):
    INIT = 0
    AUTO = 1
    MANUAL = 2

class TestWrapper(unittest.TestCase):
    def setUp(self):
        self.network = canopen.Network()
        self.network.connect(interface='virtual', channel='test_ch1', bitrate=500000, receive_own_messages=True)
        
        od = canopen.ObjectDictionary()
        
        # 0x2000 Status (Unsigned16)
        status = canopen.objectdictionary.ODVariable("Status", 0x2000, 0)
        status.data_type = canopen.objectdictionary.datatypes.UNSIGNED16
        od.add_object(status)

        # mock RPDO1 communication
        rpdo_comm = canopen.objectdictionary.ODRecord("RPDO1 Communication", 0x1400)
        cob_id = canopen.objectdictionary.ODVariable("COB-ID", 0x1400, 1)
        cob_id.data_type = canopen.objectdictionary.datatypes.UNSIGNED32
        cob_id.value = 0x201
        
        trans_type = canopen.objectdictionary.ODVariable("Trans Type", 0x1400, 2)
        trans_type.data_type = canopen.objectdictionary.datatypes.UNSIGNED8
        trans_type.value = 254
        
        rpdo_comm.add_member(cob_id)
        rpdo_comm.add_member(trans_type)
        
        rpdo_map = canopen.objectdictionary.ODArray("RPDO1 Mapping", 0x1600)
        nof_entries = canopen.objectdictionary.ODVariable("Number of entries", 0x1600, 0)
        nof_entries.data_type = canopen.objectdictionary.datatypes.UNSIGNED8
        nof_entries.value = 1
        
        entry = canopen.objectdictionary.ODVariable("Entry 1", 0x1600, 1)
        entry.data_type = canopen.objectdictionary.datatypes.UNSIGNED32
        # maps 0x2000 subindex 0, length 16 -> (0x2000 << 16) | (0 << 8) | 16
        entry.value = 0x20000010
        
        rpdo_map.add_member(nof_entries)
        rpdo_map.add_member(entry)
        
        od.add_object(rpdo_comm)
        od.add_object(rpdo_map)

        self.node = CustomNode(1, object_dictionary=od)
        self.network.add_node(self.node)
        self.node.sdo[0x2000].raw = 0

        # Map bit fields
        self.node.add_bitfield("status", "error_flag", [0], bool)
        self.node.add_bitfield("status", "mode", [1, 2], int, Mode)
        self.node.add_bitfield("status", "counter", list(range(3, 8)), int)

    def tearDown(self):
        self.network.disconnect()

    def test_bitfields(self):
        self.node.status.error_flag = True
        self.assertEqual(self.node.status.error_flag, True)
        self.assertEqual(self.node.sdo[0x2000].raw, 1)

        self.node.status.mode = Mode.AUTO
        self.assertEqual(self.node.sdo[0x2000].raw, 1 | (1<<1))

        self.node.status.counter = 5
        self.assertEqual(self.node.status.counter, 5)

    def test_rpdo_store_immediate(self):
        # Configure RPDO 1
        self.node.rpdo.read(from_od=False)
        self.node.rpdo[1].clear()
        self.node.rpdo[1].add_variable('Status')
        self.node.rpdo[1].enabled = True
        self.node.rpdo[1].cob_id = 0x200 + self.node.id
        self.node.rpdo[1].subscribe()

        # Emit an RPDO via network
        # 0x2000 is UN16 -> 2 bytes. Let's send 0x0B 0x00 -> 11 -> Error = 1, Mode = 1 (Auto), Counter = 1
        self.network.send_message(0x201, b'\x0B\x00')
        import time
        time.sleep(0.1)

        # Check if the node's local storage and named variable inherited the 11 automatically!
        # Without our fix, self.node.sdo[0x2000].raw or node.status.value would NOT show 11!
        self.assertEqual(self.node.status.value, 11)
        self.assertEqual(self.node.status.error_flag, True)
        self.assertEqual(self.node.status.mode, Mode.AUTO)


if __name__ == '__main__':
    unittest.main()
