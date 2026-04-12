import logging
from typing import Union, Dict, Any, Callable, List
from enum import Enum

from canopen.node import LocalNode
from canopen.objectdictionary import ObjectDictionary, ODVariable, ODRecord, ODArray, datatypes

logger = logging.getLogger(__name__)

def _get_canopen_type(type_name: str):
    if hasattr(datatypes, type_name):
        return getattr(datatypes, type_name)
    raise ValueError(f"Unknown CANopen datatype: {type_name}")

class ObjectDef:
    """Descriptor that bridges a declarative Python attribute directly to CANopen Dictionary bindings."""
    def __init__(self, name: str, index: int, sub: int = 0, type: str = "UNSIGNED16", default=0):
        self.name = name
        self.index = index
        self.sub = sub
        self.type_name = type
        self.default = default
        
    def _resolve_var(self, instance):
        var = instance.sdo[self.index]
        if not hasattr(var, 'raw'):
            var = var[self.sub]
        return var

    def __get__(self, instance, owner):
        if instance is None: return self
        return self._resolve_var(instance).raw

    def __set__(self, instance, value):
        self._resolve_var(instance).raw = value

class BitField:
    """Declarative descriptor mapping binary slice across an ObjectDef."""
    def __init__(self, target: ObjectDef, bits: List[int], type=int, enum_class=None):
        self.target = target
        self.bits = bits
        self.field_type = type
        self.enum_class = enum_class
        try:
            if issubclass(type, Enum):
                self.enum_class = type
        except TypeError:
            pass

    def _resolve_var(self, instance):
        var = instance.sdo[self.target.index]
        if not hasattr(var, 'bits'):
            var = var[self.target.sub]
        return var

    def __get__(self, instance, owner):
        if instance is None:
            return self
        var = self._resolve_var(instance)
        
        raw_val = var.bits[self.bits]
        if self.field_type is bool:
            return bool(raw_val)
        elif self.enum_class is not None:
            return self.enum_class(raw_val)
        return raw_val

    def __set__(self, instance, value):
        var = self._resolve_var(instance)
            
        if self.field_type is bool:
            raw_val = 1 if value else 0
        elif self.enum_class is not None:
            raw_val = value.value if isinstance(value, Enum) else int(value)
        else:
            raw_val = int(value)
            
        var.bits[self.bits] = raw_val

class RPDOMap:
    """Declarative builder for Receive PDOs."""
    def __init__(self, cob_id: int, trans_type: int, payload: List[ObjectDef]):
        self.cob_id = cob_id
        self.trans_type = trans_type
        self.payload = payload

class TPDOMap:
    """Declarative builder for Transmit PDOs."""
    def __init__(self, cob_id: int, trans_type: int, payload: List[ObjectDef]):
        self.cob_id = cob_id
        self.trans_type = trans_type
        self.payload = payload

class IndexMeta:
    """Non-functional mapping tag to explicitly label parent Arrays or Records in user interfaces."""
    def __init__(self, name: str, index: int):
        self.name = name
        self.index = index

def on_object_write(target: ObjectDef):
    """Decorator to bind business logic to an ObjectDef write event."""
    def decorator(func):
        func.__canopen_trigger__ = target
        return func
    return decorator


class BusinessLogicManager:
    """API for managing user-defined business logic hooks dynamically during tests."""
    def __init__(self, node):
        self._node = node
        self._triggers = {}  # Format: (index, subindex) -> {name: callback}
        self._disabled = set()

    def register(self, target: ObjectDef, func: Callable, name: str = None):
        """Dynamically add or modify a new business logic behavior at runtime!"""
        key = (target.index, target.sub)
        if key not in self._triggers:
            self._triggers[key] = {}
        
        hook_name = name or func.__name__
        self._triggers[key][hook_name] = func

    def unregister(self, name: str):
        """Completely remove a hook by name."""
        for funcs in self._triggers.values():
            if name in funcs:
                del funcs[name]

    def disable(self, name: str):
        """Silences a behavior rule (useful for test isolation)."""
        self._disabled.add(name)

    def enable(self, name: str):
        """Reinstates a previously disabled behavior rule."""
        self._disabled.discard(name)

    def __call__(self, index, subindex, od, data, **kwargs):
        """The core execution router fired by python-canopen callbacks."""
        key = (index, subindex)
        if key in self._triggers:
            value = od.decode_raw(data)
            for name, cb in self._triggers[key].items():
                if name not in self._disabled:
                    cb(index, subindex, value)


class DeclarativeNode(LocalNode):
    """
    A magically generated CANopen Node that inherently understands decorators,
    descriptors, and declarative attribute modeling.
    """
    def __init__(self, node_id: int):
        od = ObjectDictionary()
        
        cls = self.__class__
        
        # Base Indexes for CANopen PDOs
        rpdo_idx = 0x1400
        tpdo_idx = 0x1800
        rpdo_map_idx = 0x1600
        tpdo_map_idx = 0x1A00

        # 1. Gather all ObjectDefs to support automatic Memory Record Grouping
        from collections import defaultdict
        grouped_objects = defaultdict(list)
        index_labels = {}
        
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if isinstance(attr, ObjectDef):
                grouped_objects[attr.index].append(attr)
            elif isinstance(attr, IndexMeta):
                index_labels[attr.index] = attr.name

        # 2. Inject ObjectDefs into network memory reliably forming Variables vs Records
        for index, group in grouped_objects.items():
            if len(group) == 1 and group[0].sub == 0:
                attr = group[0]
                var = ODVariable(attr.name, attr.index, attr.sub)
                var.data_type = _get_canopen_type(attr.type_name)
                var.value = attr.default
                od.add_object(var)
            else:
                record_name = index_labels.get(index, f"Dynamic Component {hex(index)}")
                record = ODRecord(record_name, index)
                
                # Append standard subindexes
                for attr in group:
                    var = ODVariable(attr.name, attr.index, attr.sub)
                    var.data_type = _get_canopen_type(attr.type_name)
                    var.value = attr.default
                    record.add_member(var)
                
                # Provide the CANopen generic sub=0 element count automatically if the user didn't!
                if 0 not in [a.sub for a in group]:
                    num_entries = max(a.sub for a in group)
                    length_var = ODVariable("Number of Entries", index, 0)
                    length_var.data_type = _get_canopen_type("UNSIGNED8")
                    length_var.value = num_entries
                    record.add_member(length_var)
                
                od.add_object(record)

        # 3. Iterating over dynamic PDO Map builders
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            
            if isinstance(attr, RPDOMap):
                # OD Communication Array (0x14XX)
                rpdo_comm = ODRecord(f"RPDO Communication {rpdo_idx}", rpdo_idx)
                rpdo_comm.add_member(ODVariable("COB-ID", rpdo_idx, 1))
                rpdo_comm.add_member(ODVariable("Trans Type", rpdo_idx, 2))
                rpdo_comm[1].data_type = _get_canopen_type("UNSIGNED32")
                rpdo_comm[1].value = attr.cob_id
                rpdo_comm[2].data_type = _get_canopen_type("UNSIGNED8")
                rpdo_comm[2].value = attr.trans_type
                od.add_object(rpdo_comm)
                
                # OD Mapping Array (0x16XX)
                rpdo_map = ODArray(f"RPDO Mapping {rpdo_map_idx}", rpdo_map_idx)
                rpdo_map.add_member(ODVariable("Number of entries", rpdo_map_idx, 0))
                rpdo_map[0].data_type = _get_canopen_type("UNSIGNED8")
                rpdo_map[0].value = len(attr.payload)
                
                for i, obj_def in enumerate(attr.payload, 1):
                    rpdo_map.add_member(ODVariable(f"Mapped Entry {i}", rpdo_map_idx, i))
                    rpdo_map[i].data_type = _get_canopen_type("UNSIGNED32")
                    length = 16 if '16' in obj_def.type_name else (32 if '32' in obj_def.type_name else 8)
                    mapped_value = (obj_def.index << 16) | (obj_def.sub << 8) | length
                    rpdo_map[i].value = mapped_value
                
                od.add_object(rpdo_map)
                rpdo_idx += 1
                rpdo_map_idx += 1

            elif isinstance(attr, TPDOMap):
                # OD Communication Array (0x18XX)
                tpdo_comm = ODRecord(f"TPDO Communication {tpdo_idx}", tpdo_idx)
                tpdo_comm.add_member(ODVariable("COB-ID", tpdo_idx, 1))
                tpdo_comm.add_member(ODVariable("Trans Type", tpdo_idx, 2))
                tpdo_comm[1].data_type = _get_canopen_type("UNSIGNED32")
                tpdo_comm[1].value = attr.cob_id
                tpdo_comm[2].data_type = _get_canopen_type("UNSIGNED8")
                tpdo_comm[2].value = attr.trans_type
                od.add_object(tpdo_comm)
                
                # OD Mapping Array (0x1AXX)
                tpdo_map = ODArray(f"TPDO Mapping {tpdo_map_idx}", tpdo_map_idx)
                tpdo_map.add_member(ODVariable("Number of entries", tpdo_map_idx, 0))
                tpdo_map[0].data_type = _get_canopen_type("UNSIGNED8")
                tpdo_map[0].value = len(attr.payload)
                
                for i, obj_def in enumerate(attr.payload, 1):
                    tpdo_map.add_member(ODVariable(f"Mapped Entry {i}", tpdo_map_idx, i))
                    tpdo_map[i].data_type = _get_canopen_type("UNSIGNED32")
                    length = 16 if '16' in obj_def.type_name else (32 if '32' in obj_def.type_name else 8)
                    mapped_value = (obj_def.index << 16) | (obj_def.sub << 8) | length
                    tpdo_map[i].value = mapped_value

                od.add_object(tpdo_map)
                tpdo_idx += 1
                tpdo_map_idx += 1

        # Secure default internal variables for compliant CANopen Nodes!
        if 0x1017 not in od.indices:
            heartbeat = ODVariable("Producer Heartbeat Time", 0x1017, 0)
            heartbeat.data_type = _get_canopen_type("UNSIGNED16")
            heartbeat.value = 1000
            od.add_object(heartbeat)

        super().__init__(node_id, od)

        # -------------------------------------------------------------
        # Business Logic Hook Router
        # -------------------------------------------------------------
        self.logic = BusinessLogicManager(self)

        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
                # Look for bound methods that were touched by the @on_object_write decorator
                if hasattr(attr, '__canopen_trigger__'):
                    target_def = attr.__canopen_trigger__
                    self.logic.register(target_def, attr, name=attr.__name__)
            except Exception:
                pass

        # Connect the manager to python-canopen's backend!
        self.add_write_callback(self.logic)

    @property
    def network(self):
        return self._network

    @network.setter
    def network(self, value):
        """
        Intercept the CAN bus network assignment (`network.add_node()`) to perform 
        automatic "Zero-Config" binding, freeing the user from boilerplate code!
        """
        self._network = value
        if hasattr(self, 'sdo') and self.sdo: self.sdo.network = value
        if hasattr(self, 'tpdo') and self.tpdo: self.tpdo.network = value
        if hasattr(self, 'rpdo') and self.rpdo: self.rpdo.network = value
        if hasattr(self, 'nmt') and self.nmt: self.nmt.network = value
        
        # When a valid network channel is instantiated (Not None and Not Uninitialized), automatically bind and boot
        if value is not None and type(value).__name__ != '_UninitializedNetwork' and hasattr(self, 'rpdo'):
            # 1. Instruct PDO parsers to translate the Declarative Object Dictionary into active Memory blocks
            self.rpdo.read(from_od=True)
            self.tpdo.read(from_od=True)
            
            # 2. Cycle the NMT state machine upwards according to CANopen standard
            self.nmt.state = 'INITIALISING'
            self.nmt.state = 'PRE-OPERATIONAL'
            
            # 3. Intercept Network SYNC (0x80) because python-canopen's LocalNode neglects doing this natively!
            value.subscribe(0x80, self._on_sync)

    def _on_sync(self, can_id: int, data: bytearray, timestamp: float):
        """
        Evaluates network SYNC pulses to automatically fire Synchronous TPDOs (Types 1-240).
        Fills the functional gap left empty by the python-canopen library.
        """
        self._sync_counter = getattr(self, '_sync_counter', 0) + 1
        
        if hasattr(self, 'tpdo'):
            for pdo_map in self.tpdo.map.values():
                if not pdo_map.enabled:
                    continue
                    
                tt = pdo_map.trans_type
                # If transmission type relies on Nth SYNC pulses:
                if 1 <= tt <= 240:
                    if self._sync_counter % tt == 0:
                        pdo_map.transmit()

