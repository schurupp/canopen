"""
Comprehensive Demonstration of the Declarative CANopen Architecture.
Maps all 10 Edge Cases requested by the user, providing an ultimate reference manual!

1. ODVariable Object
2. ODArray Object
3. ODRecord Object
4. Bitfield mapping Array element
5. Bitfield mapping Record element
6. 4 RPDO and 4 TPDO saturated loading
7. Logic -> Trigger on ANY variable update
8. Logic -> Trigger on SPECIFIC value only
9. Logic -> Trigger on specific value AND secondary bitfield state (1 Given, Multiple Whens)
10. Logic -> Execution triggers multiple memory writes and architecture alters (Multiple Thens)
"""
import time
import sys
import os
import logging
from enum import Enum

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import canopen
from wrapper.node import DeclarativeNode, ObjectDef, BitField, RPDOMap, TPDOMap, on_object_write, IndexMeta

logging.basicConfig(level=logging.WARNING)


# Enums automatically compatible with Bitfield decoding!
class SystemState(Enum):
    INIT = 0
    READY = 1
    ACTIVE = 2
    ERROR = 3

class TitaniumMotorNode(DeclarativeNode):
    # ==============================================================
    # [1, 2, 3] OBJECT DICTIONARY DEFINITIONS
    # ==============================================================
    
    # 1. ODVariable (Flat Object directly occupying index 0x2000)
    master_command = ObjectDef("Master Command", 0x2000, sub=0, type="UNSIGNED16")
    global_fault = ObjectDef("Global Fault", 0x2001, sub=0, type="UNSIGNED16")

    # 2. ODArray (Multiple objects inherently grouped into an Array at index 0x3000 based on identical types)
    axis_data_array = IndexMeta("Axis Position Array", 0x3000)
    axis_pos_1 = ObjectDef("Axis 1 Position", 0x3000, sub=1, type="UNSIGNED32")
    axis_pos_2 = ObjectDef("Axis 2 Position", 0x3000, sub=2, type="UNSIGNED32")
    axis_pos_3 = ObjectDef("Axis 3 Position", 0x3000, sub=3, type="UNSIGNED32")

    # 3. ODRecord (Multiple disparate type objects natively grouped under 0x4000)
    motor_components = IndexMeta("Motor Status Component", 0x4000)
    motor_state = ObjectDef("Motor State", 0x4000, sub=0, type="UNSIGNED8")
    motor_temp = ObjectDef("Motor Temperature", 0x4000, sub=1, type="REAL32")
    firmware_ver = ObjectDef("Firmware Version", 0x4000, sub=2, type="UNSIGNED32", access="ro")

    # ==============================================================
    # [4, 5] BITFIELD VIRTUAL ACCESSORS
    # ==============================================================
    
    # 4. Bitfields inside an ODArray element (Extracting the MSB of a specific Array slot)
    axis_2_limit = BitField(target=axis_pos_2, bits=[31], type=bool)

    # 5. Bitfields inside an ODRecord element (Deconstructing the State Enum out of a generic Int subindex!)
    active_state = BitField(target=motor_state, bits=[0, 1, 2], type=SystemState)
    is_overheated = BitField(target=motor_state, bits=[3], type=bool)

    # ==============================================================
    # [6] EXTREME PDO CAPACITY
    # ==============================================================
    # Fully saturating all 4 RX and TX channels per Node specs
    rpdo1 = RPDOMap(0x205, 254, payload=[master_command])
    rpdo2 = RPDOMap(0x305, 254, payload=[axis_pos_1, axis_pos_2])
    rpdo3 = RPDOMap(0x405, 254, payload=[axis_pos_3])
    rpdo4 = RPDOMap(0x505, 254, payload=[global_fault])

    tpdo1 = TPDOMap(0x185, 254, payload=[motor_state])
    tpdo2 = TPDOMap(0x285, 254, payload=[axis_pos_1, axis_pos_2])
    tpdo3 = TPDOMap(0x385, 254, payload=[motor_temp])
    tpdo4 = TPDOMap(0x485, 254, payload=[firmware_ver])
    tpdo5 = TPDOMap(cob_id=0x585, trans_type=10, payload=[axis_pos_1])

    # ==============================================================
    # [7, 8, 9, 10] COMPLEX BUSINESS LOGIC EVALUATORS
    # ==============================================================
    
    # 7. Trigger on ANY change to this specific variable
    @on_object_write(axis_pos_3)
    def tracking_log(self, idx, sub, val):
        print(f"[LOGIC 7] Axis 3 shifted dynamically via CAN: {val}")

    # 8. Trigger specifically when a variable hits a SPECIFIC value
    @on_object_write(master_command)
    def halt_sequence(self, idx, sub, val):
        if val == 0xDEAD:
            print("[LOGIC 8] Emergency Halt Command (0xDEAD) Received!")
            self.global_fault = 1

    # 9. & 10. Complex Evaluation (1 Given -> Multiple Whens -> Multiple Thens)
    @on_object_write(motor_temp)
    def thermal_throttle(self, idx, sub, val):
        # 1 GIVEN: Triggered exclusively by Temperature receiving a new reading
        # MULTIPLE WHENS: If temperature exceeds 80.0 AND the state Enum Bitfield is currently set to ACTIVE
        if val > 80.0 and self.active_state == SystemState.ACTIVE:
            print(f"[LOGIC 9] Critical Overtemp ({val}C) while Motor Active! Engaging Throttle.")
            
            # MULTIPLE THENS:
            self.is_overheated = True           # Then 1. Toggle ODRecord Bitfield directly!
            self.active_state = SystemState.ERROR # Then 2. Escalate system network state
            self.axis_pos_1 = 0                 # Then 3. Zero out ODArray memory buffers
            self.axis_pos_2 = 0
            
            # Then 4. Utilize the Behavior API to deliberately kill another sub-routine!
            self.logic.disable('tracking_log') 

if __name__ == "__main__":
    network = canopen.Network()
    network.connect(interface='udp_multicast', channel='224.0.0.1', bitrate=500000)
    
    # Declaratively boots the node (Zero-Config Network triggers handle the rest!)
    node = TitaniumMotorNode(node_id=2)
    network.add_node(node)
    
    # Establish a safe initial test state utilizing native descriptor assignments!
    node.active_state = SystemState.READY
    node.motor_temp = 25.0

    print(f"TitaniumMotorNode [ID 2] Connected seamlessly. Standing By.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        network.disconnect()
