#!/usr/bin/env python3
"""Build-time RT-950 Pro cold-boot BLE initialization patch.

The RT-950 Pro requires its OEM FF31 initialization exchange after a power
cycle before the FFE1 KISS/RTX1 data path becomes operational. Generic BLE
KISS clients do not know about this vendor-specific step, so APRSdroid performs
it automatically only when the RADTEL_RT950_KISS profile is selected.

Sequence:
  1. Discover FFE0 / FFE1 / FF31.
  2. Subscribe to FFE1 notifications.
  3. Write the 20-byte OEM initialization request to FF31 WITH RESPONSE.
  4. Consume the matching 20-byte FFE1 initialization response.
  5. Mark the KISS transport ready only after both steps succeed.

Other BLE KISS profiles are unchanged.
"""
from pathlib import Path

p = Path("src/backend/BluetoothLETnc.scala")
s = p.read_text()

# Keep this exact pace line in place so the paired RTX1 build patch can still
# change RT950 no-response pacing from 20 ms to 5 ms afterwards.
pace_anchor = "\t\tval NO_RESPONSE_PACE_MS = 20L\n"
pace_insert = pace_anchor + '''\t\tval RADTEL_INIT_UUID = UUID.fromString("0000ff31-0000-1000-8000-00805f9b34fb")
\t\tval RADTEL_INIT_TIMEOUT_MS = 2500L
\t\tval RADTEL_INIT_REQUEST = Array[Byte](
\t\t\t0x3f.toByte, 0x3f.toByte, 0x3f.toByte, 0x3f.toByte,
\t\t\t0x02.toByte, 0x2e.toByte, 0x17.toByte, 0x1d.toByte,
\t\t\t0x5e.toByte, 0x57.toByte, 0x25.toByte, 0x2f.toByte,
\t\t\t0x57.toByte, 0x13.toByte, 0x62.toByte, 0x56.toByte,
\t\t\t0x04.toByte, 0x4b.toByte, 0x23.toByte, 0x42.toByte)
\t\tval RADTEL_INIT_RESPONSE = Array[Byte](
\t\t\t0x21.toByte, 0x21.toByte, 0x21.toByte, 0x21.toByte,
\t\t\t0x21.toByte, 0x20.toByte, 0x1b.toByte, 0x28.toByte,
\t\t\t0x06.toByte, 0x4e.toByte, 0x32.toByte, 0x13.toByte,
\t\t\t0x27.toByte, 0x33.toByte, 0x16.toByte, 0x19.toByte,
\t\t\t0x3f.toByte, 0x14.toByte, 0x16.toByte, 0x32.toByte)
'''
if pace_anchor not in s:
    raise SystemExit("ERROR: expected BLE pace anchor not found")
s = s.replace(pace_anchor, pace_insert, 1)

state_anchor = "\t\t@volatile var rxNotifyAttached = false\n"
state_insert = state_anchor + '''\t\t@volatile var radtelInitCharacteristic : BluetoothGattCharacteristic = null
\t\t@volatile var radtelInitPending = false
\t\t@volatile var radtelInitWriteComplete = false
\t\t@volatile var radtelInitResponseSeen = false
'''
if state_anchor not in s:
    raise SystemExit("ERROR: expected BLE state anchor not found")
s = s.replace(state_anchor, state_insert, 1)

write_anchor = "\t\tdef chooseWriteType() : Int = {\n"
write_helpers = '''\t\tdef writeCharacteristicDirectCompat(
\t\t\t\tcbGatt : BluetoothGatt,
\t\t\t\tcharacteristic : BluetoothGattCharacteristic,
\t\t\t\tdata : Array[Byte],
\t\t\t\twriteType : Int) : Boolean = {
\n\t\t\tif (cbGatt == null || characteristic == null || data == null)
\t\t\t\treturn false
\n\t\t\tif (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
\t\t\t\tcbGatt.writeCharacteristic(characteristic, data, writeType) == 0
\t\t\t} else {
\t\t\t\tcharacteristic.setWriteType(writeType)
\t\t\t\tcharacteristic.setValue(data)
\t\t\t\tcbGatt.writeCharacteristic(characteristic)
\t\t\t}
\t\t}
\n'''
if write_anchor not in s:
    raise SystemExit("ERROR: expected BLE write helper anchor not found")
s = s.replace(write_anchor, write_helpers + write_anchor, 1)

profile_anchor = "\t\tdef isRadtelProfile : Boolean = activeProfile == PROFILE_RADTEL_RT950\n"
profile_helpers = profile_anchor + '''
\t\tdef completeRadtelInitIfReady() {
\t\t\tif (!radtelInitPending || !radtelInitWriteComplete || !radtelInitResponseSeen)
\t\t\t\treturn
\n\t\t\tradtelInitPending = false
\t\t\tLog.d(TAG, "*** RT950 OEM INIT=PASS")
\t\t\tmarkTransportReady()
\t\t\tLog.d(TAG, "*** RADTEL KISS READY")
\t\t}
\n\t\tdef consumeRadtelInitNotification(data : Array[Byte]) : Boolean = {
\t\t\tif (!isRadtelProfile || !radtelInitPending || data == null)
\t\t\t\treturn false
\n\t\t\tif (java.util.Arrays.equals(data, RADTEL_INIT_RESPONSE)) {
\t\t\t\tradtelInitResponseSeen = true
\t\t\t\tLog.d(TAG,
\t\t\t\t\t"*** RT950 OEM INIT RESPONSE len=" + data.length +
\t\t\t\t\t" hex=" + bytesToHex(data))
\t\t\t\tcompleteRadtelInitIfReady()
\t\t\t\ttrue
\t\t\t} else {
\t\t\t\tfalse
\t\t\t}
\t\t}
\n\t\tdef beginRadtelInit(cbGatt : BluetoothGatt, gen : Int) {
\t\t\tval characteristic = radtelInitCharacteristic
\t\t\tif (characteristic == null) {
\t\t\t\tfailConnection("RT950 FF31 initialization characteristic missing")
\t\t\t\treturn
\t\t\t}
\n\t\t\tradtelInitPending = true
\t\t\tradtelInitWriteComplete = false
\t\t\tradtelInitResponseSeen = false
\n\t\t\tLog.d(TAG,
\t\t\t\t"*** RT950 OEM INIT WRITE uuid=FF31 len=" + RADTEL_INIT_REQUEST.length +
\t\t\t\t" hex=" + bytesToHex(RADTEL_INIT_REQUEST))
\n\t\t\tif (!writeCharacteristicDirectCompat(
\t\t\t\t\tcbGatt,
\t\t\t\t\tcharacteristic,
\t\t\t\t\tRADTEL_INIT_REQUEST,
\t\t\t\t\tBluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)) {
\t\t\t\tradtelInitPending = false
\t\t\t\tfailConnection("RT950 FF31 initialization write rejected")
\t\t\t\treturn
\t\t\t}
\n\t\t\tmainHandler.postDelayed(new Runnable {
\t\t\t\toverride def run() {
\t\t\t\t\tif (gen == generation && radtelInitPending) {
\t\t\t\t\t\tradtelInitPending = false
\t\t\t\t\t\tfailConnection("RT950 OEM initialization timed out")
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}, RADTEL_INIT_TIMEOUT_MS)
\t\t}
'''
if profile_anchor not in s:
    raise SystemExit("ERROR: expected RT950 profile helper anchor not found")
s = s.replace(profile_anchor, profile_helpers, 1)

select_anchor = '''\t\t\trxCharacteristic = selectedRx
\t\t\ttxCharacteristic = selectedTx
\n\t\t\tLog.d(TAG,
'''
select_insert = '''\t\t\trxCharacteristic = selectedRx
\t\t\ttxCharacteristic = selectedTx
\t\t\tradtelInitCharacteristic = null
\n\t\t\tif (isRadtelProfile) {
\t\t\t\tval control = selectedService.getCharacteristic(RADTEL_INIT_UUID)
\t\t\t\tif (control == null ||
\t\t\t\t    (control.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE) == 0) {
\t\t\t\t\tfailConnection("RT950 FF31 initialization characteristic unavailable")
\t\t\t\t\treturn false
\t\t\t\t}
\t\t\t\tradtelInitCharacteristic = control
\t\t\t}
\n\t\t\tLog.d(TAG,
'''
if select_anchor not in s:
    raise SystemExit("ERROR: expected profile selection anchor not found")
s = s.replace(select_anchor, select_insert, 1)

old_descriptor_ready = '''\t\t\t\tif (isRadtelProfile) {
\t\t\t\t\tLog.d(TAG, "*** FFE1 CCCD WRITE RESULT=Success")
\t\t\t\t\tLog.d(TAG, "*** FFE1 NOTIFY ACTIVE")
\t\t\t\t}
\n\t\t\t\t// A BLE connection is not transport-ready until notification CCCD
\t\t\t\t// subscription has completed successfully.
\t\t\t\tmarkTransportReady()
\n\t\t\t\tif (isRadtelProfile) {
\t\t\t\t\t// RT950/HM-10 KISS is hardware-qualified with the default 23-byte
\t\t\t\t\t// ATT MTU and 20-byte chunks. Do not impose a large MTU request.
\t\t\t\t\tLog.d(TAG, "*** RADTEL KISS READY")
\t\t\t\t} else {
\t\t\t\t\t// Existing standard/TWR behavior: become operational first, then
\t\t\t\t\t// increase TX chunk size if Android and the peripheral accept it.
\t\t\t\t\ttry cbGatt.requestMtu(517) catch {
\t\t\t\t\t\tcase _ : Throwable =>
\t\t\t\t\t}
\t\t\t\t}
'''
new_descriptor_ready = '''\t\t\t\tif (isRadtelProfile) {
\t\t\t\t\tLog.d(TAG, "*** FFE1 CCCD WRITE RESULT=Success")
\t\t\t\t\tLog.d(TAG, "*** FFE1 NOTIFY ACTIVE")
\t\t\t\t\t// RT950 requires a vendor-specific FF31 initialization exchange
\t\t\t\t\t// after every cold boot. Keep connectionActive=false until the
\t\t\t\t\t// request write and matching FFE1 response have both completed.
\t\t\t\t\tbeginRadtelInit(cbGatt, gen)
\t\t\t\t} else {
\t\t\t\t\t// A standard BLE-KISS connection becomes transport-ready once its
\t\t\t\t\t// notification subscription has completed successfully.
\t\t\t\t\tmarkTransportReady()
\t\t\t\t\ttry cbGatt.requestMtu(517) catch {
\t\t\t\t\t\tcase _ : Throwable =>
\t\t\t\t\t}
\t\t\t\t}
'''
if old_descriptor_ready not in s:
    raise SystemExit("ERROR: expected descriptor-ready block not found")
s = s.replace(old_descriptor_ready, new_descriptor_ready, 1)

old_changed_legacy = '''\t\t\t\tif (gen == generation && activeRxUuid != null &&
\t\t\t\t    characteristic.getUuid() == activeRxUuid)
\t\t\t\t\thandleRx(characteristic.getValue())
'''
new_changed_legacy = '''\t\t\t\tif (gen == generation && activeRxUuid != null &&
\t\t\t\t    characteristic.getUuid() == activeRxUuid) {
\t\t\t\t\tval data = characteristic.getValue()
\t\t\t\t\tif (!consumeRadtelInitNotification(data))
\t\t\t\t\t\thandleRx(data)
\t\t\t\t}
'''
if old_changed_legacy not in s:
    raise SystemExit("ERROR: expected legacy characteristic callback not found")
s = s.replace(old_changed_legacy, new_changed_legacy, 1)

old_changed_new = '''\t\t\t\tif (gen == generation && activeRxUuid != null &&
\t\t\t\t    characteristic.getUuid() == activeRxUuid)
\t\t\t\t\thandleRx(value)
'''
new_changed_new = '''\t\t\t\tif (gen == generation && activeRxUuid != null &&
\t\t\t\t    characteristic.getUuid() == activeRxUuid) {
\t\t\t\t\tif (!consumeRadtelInitNotification(value))
\t\t\t\t\t\thandleRx(value)
\t\t\t\t}
'''
if old_changed_new not in s:
    raise SystemExit("ERROR: expected API33 characteristic callback not found")
s = s.replace(old_changed_new, new_changed_new, 1)

old_write_callback = '''\t\t\t\tif (gen != generation || activeTxUuid == null ||
\t\t\t\t    characteristic.getUuid() != activeTxUuid)
\t\t\t\t\treturn
\n\t\t\t\tval out = output
'''
new_write_callback = '''\t\t\t\tif (gen != generation)
\t\t\t\t\treturn
\n\t\t\t\tif (isRadtelProfile && characteristic.getUuid() == RADTEL_INIT_UUID) {
\t\t\t\t\tif (!radtelInitPending)
\t\t\t\t\t\treturn
\t\t\t\t\tif (status != BluetoothGatt.GATT_SUCCESS) {
\t\t\t\t\t\tradtelInitPending = false
\t\t\t\t\t\tfailConnection("RT950 FF31 initialization write failed: " + status)
\t\t\t\t\t\treturn
\t\t\t\t\t}
\t\t\t\t\tradtelInitWriteComplete = true
\t\t\t\t\tLog.d(TAG, "*** RT950 OEM INIT WRITE COMPLETE=SUCCESS")
\t\t\t\t\tcompleteRadtelInitIfReady()
\t\t\t\t\treturn
\t\t\t\t}
\n\t\t\t\tif (activeTxUuid == null || characteristic.getUuid() != activeTxUuid)
\t\t\t\t\treturn
\n\t\t\t\tval out = output
'''
if old_write_callback not in s:
    raise SystemExit("ERROR: expected characteristic-write callback not found")
s = s.replace(old_write_callback, new_write_callback, 1)

close_anchor = '''\t\t\trxNotifyAttached = false
\n\t\t\tval guard = rxGuard
'''
close_insert = '''\t\t\trxNotifyAttached = false
\t\t\tradtelInitCharacteristic = null
\t\t\tradtelInitPending = false
\t\t\tradtelInitWriteComplete = false
\t\t\tradtelInitResponseSeen = false
\n\t\t\tval guard = rxGuard
'''
if close_anchor not in s:
    raise SystemExit("ERROR: expected closeGatt reset anchor not found")
s = s.replace(close_anchor, close_insert, 1)

p.write_text(s)
print("RT950 cold-boot OEM BLE initialization patch applied")
print("- FF31 init request: automatic after FFE1 CCCD subscription")
print("- FFE1 init response: consumed before KISS stream activation")
print("- KISS ready: only after init write + response PASS")
print("- non-RT950 BLE profiles: unchanged")
