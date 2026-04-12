"""
Raw CAN Analyzer UI.
Listens to all CAN traffic and prints it in raw hex format.
Allows sending raw CAN frames manually without input interruption!
"""

import time
import sys
import os
import threading
import msvcrt

# Ensure the parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import can

NETWORK_INTERFACE = 'udp_multicast' 
NETWORK_CHANNEL = '224.0.0.1'

# ==========================================
# Thread-safe Asynchronous Console Printing
# ==========================================
input_buffer = ""
print_lock = threading.Lock()

def safe_print(text):
    global input_buffer
    with print_lock:
        # Erase current typed line visually, print the incoming message, then redraw the prompt safely
        sys.stdout.write('\r' + ' ' * (len(input_buffer) + 15) + '\r')
        sys.stdout.write(text + '\n')
        sys.stdout.write("Analyzer> " + input_buffer)
        sys.stdout.flush()

class RawCanListener(can.Listener):
    def on_message_received(self, msg):
        hex_data = " ".join(f"{b:02X}" for b in msg.data)
        safe_print(f"[RX] ID: 0x{msg.arbitration_id:03X} | DLC: {msg.dlc} | Data: [{hex_data}]")

def process_command(cmd_str, bus):
    """Parses and executes a command string safely."""
    parts = cmd_str.split()
    if not parts:
        return False
        
    command = parts[0].lower()
    
    if command == "exit":
        return True # Signal shutdown
        
    elif command == "send" and len(parts) >= 2:
        try:
            can_id = int(parts[1], 16)
            data = [int(b, 16) for b in parts[2:]]
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=can_id > 0x7FF)
            bus.send(msg)
            safe_print(f"[TX] ID: 0x{can_id:03X} | DLC: {len(data)} | Data: [{' '.join(f'{b:02X}' for b in data)}]")
        except ValueError:
            safe_print("Format Error: Ensure ID and Bytes are in hex (like 'send 205 26 00').")
        except Exception as e:
            safe_print(f"Error: {e}")
    else:
        safe_print("Unknown command. Try 'send 205 26 00' or 'exit'.")
    return False

def main():
    print(f"Connecting Raw Analyzer to {NETWORK_INTERFACE}:{NETWORK_CHANNEL}...")
    
    try:
        bus = can.interface.Bus(interface=NETWORK_INTERFACE, channel=NETWORK_CHANNEL, bitrate=500000)
    except TypeError:
        bus = can.interface.Bus(bustype=NETWORK_INTERFACE, channel=NETWORK_CHANNEL, bitrate=500000)

    listener = RawCanListener()
    notifier = can.Notifier(bus, [listener])

    print("\n--- RAW CAN ANALYZER READY ---")
    print("Commands:")
    print("  send <ID> <Bytes> : Send raw hex frame (e.g. 'send 205 26 00')")
    print("  exit              : Quit program")
    
    global input_buffer
    sys.stdout.write("\nAnalyzer> ")
    sys.stdout.flush()
    
    try:
        # Custom input polling loop via MSVCRT to support asynchronous printing above it!
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getch()
                
                # Enter Key
                if char in (b'\r', b'\n'):
                    with print_lock:
                        sys.stdout.write('\n')
                        cmd = input_buffer.strip()
                        input_buffer = ""
                        sys.stdout.write("Analyzer> ")
                        sys.stdout.flush()
                        
                    if cmd:
                        if process_command(cmd, bus):
                            break # Exit requested
                
                # Backspace Key
                elif char == b'\x08':
                    with print_lock:
                        if len(input_buffer) > 0:
                            input_buffer = input_buffer[:-1]
                            sys.stdout.write('\b \b')
                            sys.stdout.flush()
                
                # Ctrl+C
                elif char == b'\x03':
                    raise KeyboardInterrupt
                
                # Normal text
                else:
                    try:
                        char_str = char.decode('utf-8')
                        # Ignore escape sequences from arrow keys (they come as \xe0 or \x00 pairs)
                        if char_str.isprintable():
                            with print_lock:
                                input_buffer += char_str
                                sys.stdout.write(char_str)
                                sys.stdout.flush()
                    except UnicodeDecodeError:
                        pass
            
            time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        notifier.stop()
        bus.shutdown()
        print("\nShutdown.")

if __name__ == "__main__":
    main()
