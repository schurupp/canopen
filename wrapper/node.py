from __future__ import annotations
import logging
from typing import Union, Dict, Any, Callable
from enum import Enum

from canopen.node import LocalNode
from canopen.objectdictionary import ObjectDictionary, ODVariable, ODRecord, ODArray
from canopen.variable import Variable
from canopen.sdo import SdoAbortedError

logger = logging.getLogger(__name__)

class BitFieldDef:
    """Definition for a sub-byte field."""
    def __init__(self, name: str, bits: list[int], field_type: type = int, enum_class=None):
        self.name = name
        self.bits = bits
        self.field_type = field_type
        self.enum_class = enum_class

class NamedVariable:
    """Wraps an ODVariable or ODRecord/ODArray for name-based and bit-field access."""
    def __init__(self, node: CustomNode, od_entry: Union[ODVariable, ODRecord, ODArray]):
        self._node = node
        self._od_entry = od_entry
        self._bit_definitions: Dict[str, BitFieldDef] = {}

    def _add_bitfield(self, bit_def: BitFieldDef):
        self._bit_definitions[bit_def.name] = bit_def

    def __getattr__(self, name: str):
        # 1. Check if it's a bitfield
        if name in self._bit_definitions:
            if not isinstance(self._od_entry, ODVariable):
                raise TypeError("Bitfields only applicable to ODVariable")
            bit_def = self._bit_definitions[name]
            var = self._node.sdo[self._od_entry.index]
            if isinstance(self._od_entry, ODVariable) and self._od_entry.subindex > 0:
                 var = self._node.sdo[self._od_entry.index][self._od_entry.subindex]
            
            raw_val = var.bits[bit_def.bits]
            if bit_def.field_type is bool:
                return bool(raw_val)
            elif bit_def.enum_class is not None:
                return bit_def.enum_class(raw_val)
            return raw_val

        # 2. Check if it's a sub-entry for ODRecord/ODArray
        if isinstance(self._od_entry, (ODRecord, ODArray)):
            for sub_id, sub_od in self._od_entry.subindices.items():
                if sub_od.name == name or sub_od.name.endswith('.' + name):
                    return NamedVariable(self._node, sub_od)

        raise AttributeError(f"'{self._od_entry.name}' has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        # 1. Internal variables
        if name.startswith('_'):
            super().__setattr__(name, value)
            return

        # 2. Bitfield dynamic sets
        if name in self._bit_definitions:
            if not isinstance(self._od_entry, ODVariable):
                raise TypeError("Bitfields only applicable to ODVariable")
            bit_def = self._bit_definitions[name]
            var = self._node.sdo[self._od_entry.index]
            if isinstance(self._od_entry, ODVariable) and self._od_entry.subindex > 0:
                 var = self._node.sdo[self._od_entry.index][self._od_entry.subindex]
            
            if bit_def.field_type is bool:
                raw_val = 1 if value else 0
            elif bit_def.enum_class is not None:
                raw_val = value.value if isinstance(value, Enum) else int(value)
            else:
                raw_val = int(value)
            
            var.bits[bit_def.bits] = raw_val
            return

        # 3. Normal attributes (Allows @value.setter to trigger instead of crashing)
        super().__setattr__(name, value)

    @property
    def value(self):
        if isinstance(self._od_entry, ODVariable):
            var = self._node.sdo[self._od_entry.index]
            if self._od_entry.subindex > 0:
                var = self._node.sdo[self._od_entry.index][self._od_entry.subindex]
            return var.raw
        raise TypeError("Can only read value of a Variable, not Record/Array")

    @value.setter
    def value(self, val):
        if isinstance(self._od_entry, ODVariable):
            var = self._node.sdo[self._od_entry.index]
            if self._od_entry.subindex > 0:
                var = self._node.sdo[self._od_entry.index][self._od_entry.subindex]
            var.raw = val
        else:
            raise TypeError("Can only write value of a Variable, not Record/Array")


class CustomNode(LocalNode):
    """Declarative node wrapper with name-based access and business logic handles."""

    def __init__(self, node_id: int, object_dictionary: Union[ObjectDictionary, str]):
        super().__init__(node_id, object_dictionary)
        self._named_vars: Dict[str, NamedVariable] = {}
        self._logic_callbacks: list[Callable] = []
        
        # Populate named variables cache
        for obj in self.object_dictionary.indices.values():
            name = obj.name.replace(' ', '_').lower() # Normalize name
            self._named_vars[name] = NamedVariable(self, obj)

        # Hook into LocalNode's write capabilities to power `on_object_update`
        self.add_write_callback(self._business_logic_hook)

    def __getattr__(self, name: str):
        named_vars = self.__dict__.get('_named_vars')
        if named_vars is not None and name in named_vars:
            return named_vars[name]
        raise AttributeError(f"'CustomNode' object has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        named_vars = self.__dict__.get('_named_vars')
        if named_vars is not None and name in named_vars:
            named_vars[name].value = value
            return
        super().__setattr__(name, value)

    def _business_logic_hook(self, index, subindex, od, data, **kwargs):
        value = od.decode_raw(data)
        for cb in self._logic_callbacks:
            cb(index, subindex, value)

    def register_logic(self, pdo_tx=None, pdo_rx=None, on_object_update=None):
        if on_object_update:
            self._logic_callbacks.append(on_object_update)
        
    def add_bitfield(self, obj_name: str, name: str, bits: list[int], field_type: type = int, enum_class=None):
        """Programmatic way to attach a declaratively defined bit field."""
        if obj_name in self._named_vars:
            self._named_vars[obj_name]._add_bitfield(BitFieldDef(name, bits, field_type, enum_class))

    @classmethod
    def from_generated(cls, node_definition: str, business_logic=None):
        import importlib.util
        spec = importlib.util.spec_from_file_location("node_def", node_definition)
        node_def = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(node_def)
        
        # Expects node_def to define NODE_ID, EDS_PATH or ObjectDictionary
        node = cls(node_def.NODE_ID, node_def.OD)
        
        if hasattr(node_def, 'setup_bitfields'):
            node_def.setup_bitfields(node)

        if business_logic:
            spec = importlib.util.spec_from_file_location("logic_def", business_logic)
            logic = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(logic)
            node.register_logic(
                on_object_update=getattr(logic, 'on_object_update', None)
            )

        return node
