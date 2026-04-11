"""
This example demonstrates a complete CANopen slave node using the new `wrapper` features:
- Declarative properties and Bitfield slices (Enums, Booleans)
- Declarative Business Logic hooks on OD changes
- Real-time PDO synchronization (RPDO -> Node -> TPDO reflections)
- Background Event-Driven PDO TX

Instructions for SavvyCAN:
1. Open SavvyCAN and connect to your CAN adapter (e.g. IXXAT, PCAN).
2. Change the `NETWORK_INTERFACE` and `NETWORK_CHANNEL` in this script to match your hardware.
   - For IXXAT: `NETWORK_INTERFACE = 'ixxat'`, `NETWORK_CHANNEL = 0`
   - For PCAN: `NETWORK_INTERFACE = 'pcan'`, `NETWORK_CHANNEL = 'PCAN_USBBUS1'`
3. Run this script. You will see heartbeat messages on ID `0x705`.
4. Send an RPDO in SavvyCAN using sender panel:
   - ID: 0x205
   - Length: 2
   - Hex Data: 26 00 (Decodes to -> Fault: False, State: ACTIVE, Dir: FORWARD, Speed: 4)
5. Watch the Custom Node automatically parse bits, run engine logic, update temperature mapped metrics, and autonomously reply via TPDO1 (0x185) instantly upon reacting!
"""
import time
import logging
from enum import Enum
import sys
import os

# Add the parent directory to sys.path so the 'wrapper' module can be found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import canopen
from wrapper.node import CustomNode

logging.basicConfig(level=logging.WARNING)

# ==========================================
# 1. ENUMS & CONSTANTS
# ==========================================
class DeviceState(Enum):
    INIT = 0
    READY = 1
    ACTIVE = 2
    ERROR = 3

class MotorDirection(Enum):
    FORWARD = 0
    REVERSE = 1

# ==========================================
# 2. Setup Object Dictionary
# ==========================================
od = canopen.ObjectDictionary()

# --- Heartbeat Configuration (0x1017) ---
heartbeat = canopen.objectdictionary.ODVariable("Producer Heartbeat Time", 0x1017, 0)
heartbeat.data_type = canopen.objectdictionary.datatypes.UNSIGNED16
heartbeat.value = 1000 # 1000ms
od.add_object(heartbeat)

# --- App Objects ---
# Status Object (0x2000)
status_obj = canopen.objectdictionary.ODVariable("Device Status", 0x2000, 0)
status_obj.data_type = canopen.objectdictionary.datatypes.UNSIGNED16
status_obj.value = 0
od.add_object(status_obj)

# Temperature Object (0x2001)
temp_obj = canopen.objectdictionary.ODVariable("Temperature", 0x2001, 0)
temp_obj.data_type = canopen.objectdictionary.datatypes.REAL32
temp_obj.value = 25.0
od.add_object(temp_obj)

# ==========================================
# 3. Setup PDO Mappings
# ==========================================
def setup_pdo_mapping(od_ref):
    # --- RPDO 1: Incoming Commands mapped to 0x2000 ---
    rpdo1_comm = canopen.objectdictionary.ODRecord("RPDO1 Communication", 0x1400)
    rpdo1_comm.add_member(canopen.objectdictionary.ODVariable("COB-ID", 0x1400, 1))
    rpdo1_comm.add_member(canopen.objectdictionary.ODVariable("Trans Type", 0x1400, 2))
    rpdo1_comm[1].data_type = canopen.objectdictionary.datatypes.UNSIGNED32
    rpdo1_comm[1].value = 0x205 # Listen on 0x205
    rpdo1_comm[2].data_type = canopen.objectdictionary.datatypes.UNSIGNED8
    rpdo1_comm[2].value = 254 # Event-driven
    
    rpdo1_map = canopen.objectdictionary.ODArray("RPDO1 Mapping", 0x1600)
    rpdo1_map.add_member(canopen.objectdictionary.ODVariable("Number of entries", 0x1600, 0))
    rpdo1_map.add_member(canopen.objectdictionary.ODVariable("Mapped Entry 1", 0x1600, 1))
    rpdo1_map[0].data_type = canopen.objectdictionary.datatypes.UNSIGNED8
    rpdo1_map[0].value = 1
    rpdo1_map[1].data_type = canopen.objectdictionary.datatypes.UNSIGNED32
    # Map 0x2000, subindex 0, length 16 bits -> 0x20000010
    rpdo1_map[1].value = 0x20000010
    
    od_ref.add_object(rpdo1_comm)
    od_ref.add_object(rpdo1_map)

    # --- TPDO 1: Outgoing Data mapped to 0x2000 and 0x2001 ---
    tpdo1_comm = canopen.objectdictionary.ODRecord("TPDO1 Communication", 0x1800)
    tpdo1_comm.add_member(canopen.objectdictionary.ODVariable("COB-ID", 0x1800, 1))
    tpdo1_comm.add_member(canopen.objectdictionary.ODVariable("Trans Type", 0x1800, 2))
    tpdo1_comm[1].data_type = canopen.objectdictionary.datatypes.UNSIGNED32
    tpdo1_comm[1].value = 0x185 # Transmit on 0x185
    tpdo1_comm[2].data_type = canopen.objectdictionary.datatypes.UNSIGNED8
    tpdo1_comm[2].value = 254 # Event-driven! Any write to mapped objects triggers TX
    
    tpdo1_map = canopen.objectdictionary.ODArray("TPDO1 Mapping", 0x1A00)
    tpdo1_map.add_member(canopen.objectdictionary.ODVariable("Number of entries", 0x1A00, 0))
    tpdo1_map.add_member(canopen.objectdictionary.ODVariable("Mapped Entry 1", 0x1A00, 1))
    tpdo1_map.add_member(canopen.objectdictionary.ODVariable("Mapped Entry 2", 0x1A00, 2))
    tpdo1_map[0].data_type = canopen.objectdictionary.datatypes.UNSIGNED8
    tpdo1_map[0].value = 2 # 2 objects mapped
    tpdo1_map[1].data_type = canopen.objectdictionary.datatypes.UNSIGNED32
    tpdo1_map[1].value = 0x20000010 # 0x2000:00 (16 bits)
    tpdo1_map[2].data_type = canopen.objectdictionary.datatypes.UNSIGNED32
    tpdo1_map[2].value = 0x20010020 # 0x2001:00 (32 bits)

    od_ref.add_object(tpdo1_comm)
    od_ref.add_object(tpdo1_map)

setup_pdo_mapping(od)

# ==========================================
# 4. Construct Wrapped Node & Business Logic
# ==========================================
NODE_ID = 5
node = CustomNode(NODE_ID, object_dictionary=od)

# Declare bitfield wrappers on the status object
# 16-bit status layout:
# Bit 0    : System Fault Boolean
# Bits 1-2 : DeviceState Enum (mapping to 0-3)
# Bit 3    : Direction Enum (mapping to 0-1)
# Bits 4-8 : Integer Counter (0-31 range)
node.add_bitfield("device_status", "fault", [0], bool)
node.add_bitfield("device_status", "state", [1, 2], int, DeviceState)
node.add_bitfield("device_status", "direction", [3], int, MotorDirection)
node.add_bitfield("device_status", "speed_step", list(range(4, 9)), int)

# Business logic reacting to any network update
def on_app_update(index, subindex, value):
    if index == 0x2000:
        print(f"\n--> [Logic Engine] Incoming SDO/RPDO update on 0x2000 parsed!")
        print(f"     Fault Mode  : {node.device_status.fault}")
        print(f"     System State: {node.device_status.state.name}")
        print(f"     Direction   : {node.device_status.direction.name}")
        print(f"     Speed Step  : {node.device_status.speed_step}")
        
        # Make a reactionary decision modifying state
        if node.device_status.state == DeviceState.ACTIVE:
            # We heat up while active! Writing to a mapped TPDO object automatically
            # transmits the Event-driven TPDO1 across the bus instantly because of our changes!
            node.temperature.value += 1.5 
            print(f"     * Heated up to {node.temperature.value:.1f} C (Transmitting TPDO1 0x185)")

node.register_logic(on_object_update=on_app_update)


# ==========================================
# 5. Connect to Bus and Run
# ==========================================
if __name__ == "__main__":
    network = canopen.Network()
    
    # -------------------------------------------------------------
    # SAVVYCAN USER INSTRUCTIONS:
    # Change these variables to bridge local node -> SavvyCAN interface
    # For IXXAT plugin -> interface='ixxat', channel=0
    # For PCAN adapter -> interface='pcan', channel='PCAN_USBBUS1' 
    # For UDP          -> interface='udp_multicast', channel=''
    # -------------------------------------------------------------
    NETWORK_INTERFACE = 'udp_multicast' # Replace with 'ixxat', 'pcan', etc. to hook into SavvyCAN
    NETWORK_CHANNEL = '224.0.0.1'
    
    network.connect(interface=NETWORK_INTERFACE, channel=NETWORK_CHANNEL, bitrate=500000, receive_own_messages=False)
    
    network.add_node(node)

    # Initialize node state natively via the wrapper properties!
    node.device_status.fault = False
    node.device_status.state = DeviceState.INIT
    node.device_status.direction = MotorDirection.FORWARD
    node.device_status.speed_step = 0

    # Tell the Node to start the CANopen NMT and Heartbeats (0x705 will begin broadcasting)
    # The CANopen specification mandates transitioning to PRE-OPERATIONAL to spin up timers!
    node.nmt.state = 'PRE-OPERATIONAL'
    node.nmt.state = 'OPERATIONAL'
    
    # Reload PDOs from Object Dictionary to enable subscriptions
    node.rpdo.read(from_od=True)
    node.tpdo.read(from_od=True)

    print(f"\nNode {NODE_ID} Operational on '{NETWORK_INTERFACE}' (channel={NETWORK_CHANNEL})")
    print("---------------------------------------------------------------")
    print("Test in SavvyCAN:")
    print("1. Observe 0x705 Heartbeats every second.")
    print("2. Send an RPDO1 frame:")
    print("   [ID: 0x205] [Data Length: 2] [Data: 0x26 0x00]")
    print("3. Observe the logic print statements in this console")
    print("4. Watch as TPDO1 frame emissions pop out on ID 0x185 carrying the new temperature payload.")
    print("Press CTRL+C to exit.\n")
    
    try:
        while True:
            # Main event loop doesn't need to do anything since callbacks operate 
            # in background via `python-can` Notifier / Threads.
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        network.disconnect()
        print("Disconnected.")
