import canopen

class SharedBusDecorator:
    """
    A lightweight, decoupled shared bus proxy designed to get around single hardware
    locking mechanisms like IXXAT. It allows the Python CAN object to broadcast
    messages over UDP or IPC.
    NOTE: For windows / IXXAT, if python-can-remote or socketcan isn't used natively,
    you can decouple it here where one python program holds `can.interface.Bus('ixxat')`
    and others hold a UDP proxy over UDP/ZMQ.
    """
    pass
