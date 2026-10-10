//! Narrow, shared-background DirectInput reader. Only an explicitly selected game-controller
//! axis is requested; this module has no input-output, button, keyboard or force-feedback calls.

use std::ffi::c_void;
use std::ptr;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{HINSTANCE, HWND};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DestroyWindow, WINDOW_EX_STYLE, WS_OVERLAPPED,
};
use windows::core::{GUID, PCWSTR};

use crate::wheel::{WheelDevice, WheelInputSource, WheelReading};

const GAME_CONTROLLERS: u32 = 4;
const ATTACHED_ONLY: u32 = 1;
const NONEXCLUSIVE_BACKGROUND: u32 = 0x02 | 0x08;
const ABSOLUTE_AXIS_ANY_INSTANCE: u32 = 0x00ff_ff02;
const INPUT_IID: GUID = GUID::from_u128(0xbf798031_483a_4da2_aa99_5d64ed369700);
static AXES: [GUID; 6] = [
    GUID::from_u128(0xa36d02e0_c9f3_11cf_bfc7_444553540000),
    GUID::from_u128(0xa36d02e1_c9f3_11cf_bfc7_444553540000),
    GUID::from_u128(0xa36d02e2_c9f3_11cf_bfc7_444553540000),
    GUID::from_u128(0xa36d02f4_c9f3_11cf_bfc7_444553540000),
    GUID::from_u128(0xa36d02f5_c9f3_11cf_bfc7_444553540000),
    GUID::from_u128(0xa36d02e3_c9f3_11cf_bfc7_444553540000),
];

// Reviewed against Microsoft's WinSDK dinput.h IDirectInput8W/IDirectInputDevice8W declarations.
// Tests compare every used entry with the generated SDK bindings before fake COM dispatch.
mod slots {
    pub const RELEASE: usize = 2;
    pub const CREATE_DEVICE: usize = 3;
    pub const ENUM_DEVICES: usize = 4;
    pub const DEVICE_STATUS: usize = 5;
    pub const ACQUIRE: usize = 7;
    pub const UNACQUIRE: usize = 8;
    pub const GET_STATE: usize = 9;
    pub const SET_FORMAT: usize = 11;
    pub const COOPERATIVE_LEVEL: usize = 13;
    pub const POLL: usize = 25;
}

#[repr(C)]
struct DeviceInstance {
    size: u32,
    instance: GUID,
    product: GUID,
    device_type: u32,
    instance_name: [u16; 260],
    product_name: [u16; 260],
    force_feedback_driver: GUID,
    usage_page: u16,
    usage: u16,
}

#[repr(C)]
struct ObjectFormat {
    guid: *const GUID,
    offset: u32,
    object_type: u32,
    flags: u32,
}

#[repr(C)]
struct DataFormat {
    size: u32,
    object_size: u32,
    flags: u32,
    data_size: u32,
    count: u32,
    objects: *const ObjectFormat,
}

type Simple = unsafe extern "system" fn(*mut c_void) -> i32;
type Release = unsafe extern "system" fn(*mut c_void) -> u32;
type EnumCallback = unsafe extern "system" fn(*const DeviceInstance, *mut c_void) -> i32;
type EnumDevices =
    unsafe extern "system" fn(*mut c_void, u32, EnumCallback, *mut c_void, u32) -> i32;
type CreateDevice =
    unsafe extern "system" fn(*mut c_void, *const GUID, *mut *mut c_void, *mut c_void) -> i32;
type CooperativeLevel = unsafe extern "system" fn(*mut c_void, isize, u32) -> i32;
type SetFormat = unsafe extern "system" fn(*mut c_void, *const DataFormat) -> i32;
type GetState = unsafe extern "system" fn(*mut c_void, u32, *mut c_void) -> i32;
type DeviceStatus = unsafe extern "system" fn(*mut c_void, *const GUID) -> i32;

#[link(name = "dinput8")]
unsafe extern "system" {
    fn DirectInput8Create(
        instance: *mut c_void,
        version: u32,
        id: *const GUID,
        input: *mut *mut c_void,
        outer: *mut c_void,
    ) -> i32;
}

/// Created and used exclusively by the recorder's worker. Construction acquires no hardware.
pub struct DirectInputWheelSource {
    input: *mut c_void,
    device: *mut c_void,
    window: HWND,
    selected: GUID,
    axis: usize,
    acquired: bool,
    next_acquire: Instant,
    next_presence: Instant,
    presence: i32,
    last_error: Option<String>,
}

// SAFETY: ownership may move to the worker, but the trait exposes only mutable access; neither
// interface pointer is shared. DirectInput does not require a COM apartment.
unsafe impl Send for DirectInputWheelSource {}

impl Default for DirectInputWheelSource {
    fn default() -> Self {
        Self::new()
    }
}

impl DirectInputWheelSource {
    pub fn new() -> Self {
        Self {
            input: ptr::null_mut(),
            device: ptr::null_mut(),
            window: HWND::default(),
            selected: GUID::zeroed(),
            axis: 0,
            acquired: false,
            next_acquire: Instant::now(),
            next_presence: Instant::now(),
            presence: 1,
            last_error: None,
        }
    }

    fn ensure_input(&mut self) -> Result<(), String> {
        if !self.input.is_null() {
            return Ok(());
        }
        // SAFETY: DLL ABI and GUID match IDirectInput8W; the out pointer is valid and owned here.
        unsafe {
            let module = GetModuleHandleW(PCWSTR::null()).map_err(|e| e.to_string())?;
            check(DirectInput8Create(
                module.0,
                0x0800,
                &INPUT_IID,
                &mut self.input,
                ptr::null_mut(),
            ))
        }
    }

    fn release_device(&mut self) {
        if !self.device.is_null() {
            // SAFETY: device is the owned IDirectInputDevice8W pointer. Slots 8/2 are
            // Unacquire/Release. No pointer remains after Release.
            unsafe {
                let unacquire: Simple = std::mem::transmute(method(self.device, slots::UNACQUIRE));
                let release: Release = std::mem::transmute(method(self.device, slots::RELEASE));
                let _ = unacquire(self.device);
                let _ = release(self.device);
            }
            self.device = ptr::null_mut();
        }
    }

    fn ensure_window(&mut self) -> Result<(), String> {
        if !self.window.is_invalid() {
            return Ok(());
        }
        let class = super::wide("STATIC");
        let title = super::wide("Coach wheel input owner");
        // SAFETY: creates an invisible, worker-owned top-level window using the system STATIC
        // class. It remains valid until this source drops, even after the public UI closes.
        self.window = unsafe {
            let module = GetModuleHandleW(PCWSTR::null()).map_err(|e| e.to_string())?;
            CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                PCWSTR(class.as_ptr()),
                PCWSTR(title.as_ptr()),
                WS_OVERLAPPED,
                0,
                0,
                0,
                0,
                None,
                None,
                Some(HINSTANCE(module.0)),
                None,
            )
        }
        .map_err(|e| e.to_string())?;
        Ok(())
    }
}

impl WheelInputSource for DirectInputWheelSource {
    fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
        self.ensure_input()?;
        let mut devices = Vec::new();
        // SAFETY: DirectInput invokes the callback synchronously. The context is our writable
        // Vec for the call's lifetime; only attached game controllers are enumerated.
        unsafe {
            let enumerate: EnumDevices =
                std::mem::transmute(method(self.input, slots::ENUM_DEVICES));
            check(enumerate(
                self.input,
                GAME_CONTROLLERS,
                enumerate_device,
                (&mut devices as *mut Vec<WheelDevice>).cast(),
                ATTACHED_ONLY,
            ))?;
        }
        Ok(devices)
    }

    fn select(&mut self, device_id: &str, axis: u8, window: isize) -> Result<(), String> {
        validate_selection(axis, window)?;
        let selected =
            GUID::try_from(device_id).map_err(|_| "Invalid controller ID.".to_string())?;
        // Reject IDs for devices outside the bounded, game-controller-only picker.
        if !self
            .enumerate()?
            .iter()
            .any(|device| device.id.eq_ignore_ascii_case(device_id))
        {
            return Err("Selected game controller is not attached.".into());
        }
        self.release_device();
        self.ensure_window()?;
        self.selected = selected;
        self.axis = usize::from(axis);
        self.acquired = false;
        self.next_acquire = Instant::now();
        self.next_presence = Instant::now();
        self.last_error = None;
        // SAFETY: exact DirectInput slots/ABIs. Format buffers live throughout SetDataFormat,
        // which copies them. The format contains one selected axis, no other input objects.
        let result = unsafe {
            let create: CreateDevice =
                std::mem::transmute(method(self.input, slots::CREATE_DEVICE));
            check(create(
                self.input,
                &self.selected,
                &mut self.device,
                ptr::null_mut(),
            ))?;
            let cooperative: CooperativeLevel =
                std::mem::transmute(method(self.device, slots::COOPERATIVE_LEVEL));
            let objects = axis_formats(self.axis);
            let format = DataFormat {
                size: size_of::<DataFormat>() as u32,
                object_size: size_of::<ObjectFormat>() as u32,
                flags: 1, // DIDF_ABSAXIS.
                data_size: 4,
                count: 1,
                objects: objects.as_ptr(),
            };
            let set_format: SetFormat = std::mem::transmute(method(self.device, slots::SET_FORMAT));
            check(cooperative(
                self.device,
                self.window.0 as isize,
                NONEXCLUSIVE_BACKGROUND,
            ))
            .and_then(|()| check(set_format(self.device, &format)))
        };
        if result.is_err() {
            self.release_device();
        }
        result
    }

    fn read(&mut self) -> WheelReading {
        if self.device.is_null() {
            return reading("Not selected", None, None);
        }
        let now = Instant::now();
        if now >= self.next_presence {
            // SAFETY: owned input interface, selected instance GUID and documented slot 5.
            self.presence = unsafe {
                let status: DeviceStatus =
                    std::mem::transmute(method(self.input, slots::DEVICE_STATUS));
                status(self.input, &self.selected)
            };
            self.next_presence = now + Duration::from_secs(1);
        }
        if let Some(unavailable) = presence_reading(self.presence) {
            self.acquired = false;
            return unavailable;
        }
        if !self.acquired && now < self.next_acquire {
            return reading("Read unavailable", None, self.last_error.clone());
        }
        let mut axis = 0_i32;
        // SAFETY: all calls target the selected shared device; output is exactly four bytes as
        // declared by our axis-only format. Slots 7/25/9 are Acquire/Poll/GetDeviceState.
        let result = unsafe {
            let acquire: Simple = std::mem::transmute(method(self.device, slots::ACQUIRE));
            let poll: Simple = std::mem::transmute(method(self.device, slots::POLL));
            let state: GetState = std::mem::transmute(method(self.device, slots::GET_STATE));
            let mut result = 0;
            if !self.acquired {
                result = acquire(self.device);
                self.next_acquire = now + Duration::from_secs(1);
                self.acquired = result >= 0;
            }
            if result >= 0 {
                result = poll(self.device);
            }
            if result >= 0 {
                result = state(self.device, 4, (&mut axis as *mut i32).cast());
            }
            result
        };
        if result < 0 {
            self.acquired = false;
            self.next_acquire = now + Duration::from_secs(1);
            self.last_error = Some(native_error(result));
            return reading("Read unavailable", None, self.last_error.clone());
        }
        self.last_error = None;
        reading("Available", Some(axis), None)
    }
}

impl Drop for DirectInputWheelSource {
    fn drop(&mut self) {
        self.release_device();
        if !self.input.is_null() {
            // SAFETY: release the input interface's owned reference once.
            unsafe {
                let release: Release = std::mem::transmute(method(self.input, slots::RELEASE));
                let _ = release(self.input);
            }
        }
        if !self.window.is_invalid() {
            // SAFETY: source owns this window on its reader thread; it has outlived the device.
            unsafe {
                let _ = DestroyWindow(self.window);
            }
        }
    }
}

fn validate_selection(axis: u8, window: isize) -> Result<(), String> {
    if axis > 5 {
        return Err("Axis must be X, Y, Z, Rx, Ry or Rz.".into());
    }
    if window == 0 {
        return Err("A native Coach window is required.".into());
    }
    Ok(())
}

fn axis_formats(selected: usize) -> [ObjectFormat; 1] {
    [ObjectFormat {
        guid: &AXES[selected],
        offset: 0,
        object_type: ABSOLUTE_AXIS_ANY_INSTANCE,
        flags: 0,
    }]
}

fn presence_reading(result: i32) -> Option<WheelReading> {
    match result {
        0 => None,                                                            // DI_OK: attached.
        1 => Some(reading("Not attached", None, Some(native_error(result)))), // DI_NOTATTACHED.
        _ => Some(reading(
            "Read unavailable",
            None,
            Some(native_error(result)),
        )),
    }
}

fn reading(availability: &str, axis: Option<i32>, native_error: Option<String>) -> WheelReading {
    WheelReading {
        availability: availability.into(),
        axis,
        native_error,
    }
}

fn native_error(result: i32) -> String {
    format!("0x{:08X}", result as u32)
}
fn check(result: i32) -> Result<(), String> {
    if result < 0 {
        Err(format!(
            "DirectInput unavailable ({}).",
            native_error(result)
        ))
    } else {
        Ok(())
    }
}

// SAFETY: called only with an owned DirectInput interface and one of its documented slots.
unsafe fn method(instance: *mut c_void, slot: usize) -> *const c_void {
    unsafe {
        let table = *(instance as *const *const *const c_void);
        *table.add(slot)
    }
}

unsafe extern "system" fn enumerate_device(
    info: *const DeviceInstance,
    context: *mut c_void,
) -> i32 {
    // SAFETY: EnumDevices supplies a valid DeviceInstance and the synchronous caller's Vec.
    let (info, devices) = unsafe { (&*info, &mut *context.cast::<Vec<WheelDevice>>()) };
    if devices.len() < 32 {
        let end = info
            .product_name
            .iter()
            .position(|c| *c == 0)
            .unwrap_or(260);
        let name = String::from_utf16_lossy(&info.product_name[..end]);
        let name: String = name.chars().filter(|c| !c.is_control()).take(120).collect();
        devices.push(WheelDevice {
            id: format!("{:?}", info.instance),
            name: name.trim().to_string(),
            product_guid: Some(format!("{:?}", info.product)),
        });
    }
    i32::from(devices.len() < 32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{align_of, offset_of};
    use windows::Win32::Devices::HumanInterfaceDevice as sdk;

    fn assert_sdk_abi() {
        let pointer = size_of::<*const c_void>();
        assert_eq!(
            slots::RELEASE,
            offset_of!(windows::core::IUnknown_Vtbl, Release) / pointer
        );
        for (slot, offset) in [
            (
                slots::CREATE_DEVICE,
                offset_of!(sdk::IDirectInput8W_Vtbl, CreateDevice),
            ),
            (
                slots::ENUM_DEVICES,
                offset_of!(sdk::IDirectInput8W_Vtbl, EnumDevices),
            ),
            (
                slots::DEVICE_STATUS,
                offset_of!(sdk::IDirectInput8W_Vtbl, GetDeviceStatus),
            ),
            (
                slots::ACQUIRE,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, Acquire),
            ),
            (
                slots::UNACQUIRE,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, Unacquire),
            ),
            (
                slots::GET_STATE,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, GetDeviceState),
            ),
            (
                slots::SET_FORMAT,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, SetDataFormat),
            ),
            (
                slots::COOPERATIVE_LEVEL,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, SetCooperativeLevel),
            ),
            (
                slots::POLL,
                offset_of!(sdk::IDirectInputDevice8W_Vtbl, Poll),
            ),
        ] {
            assert_eq!(slot, offset / pointer);
        }
        assert_ne!(
            slots::DEVICE_STATUS,
            offset_of!(sdk::IDirectInput8W_Vtbl, RunControlPanel) / pointer
        );
        macro_rules! fields_match {
            ($ours:ty, $sdk:ty; $($field:ident => $sdk_field:ident),+ $(,)?) => {
                assert_eq!(size_of::<$ours>(), size_of::<$sdk>());
                assert_eq!(align_of::<$ours>(), align_of::<$sdk>());
                $(assert_eq!(offset_of!($ours, $field), offset_of!($sdk, $sdk_field));)+
            };
        }
        fields_match!(DeviceInstance, sdk::DIDEVICEINSTANCEW;
            size=>dwSize, instance=>guidInstance, product=>guidProduct, device_type=>dwDevType,
            instance_name=>tszInstanceName, product_name=>tszProductName,
            force_feedback_driver=>guidFFDriver, usage_page=>wUsagePage, usage=>wUsage);
        fields_match!(ObjectFormat, sdk::DIOBJECTDATAFORMAT;
            guid=>pguid, offset=>dwOfs, object_type=>dwType, flags=>dwFlags);
        fields_match!(DataFormat, sdk::DIDATAFORMAT;
            size=>dwSize, object_size=>dwObjSize, flags=>dwFlags, data_size=>dwDataSize,
            count=>dwNumObjs, objects=>rgodf);
        assert_eq!(
            INPUT_IID,
            <sdk::IDirectInput8W as windows::core::Interface>::IID
        );
        assert_eq!(
            AXES,
            [
                sdk::GUID_XAxis,
                sdk::GUID_YAxis,
                sdk::GUID_ZAxis,
                sdk::GUID_RxAxis,
                sdk::GUID_RyAxis,
                sdk::GUID_RzAxis
            ]
        );
        assert_eq!(GAME_CONTROLLERS, sdk::DI8DEVCLASS_GAMECTRL);
        assert_eq!(ATTACHED_ONLY, sdk::DIEDFL_ATTACHEDONLY);
        assert_eq!(
            NONEXCLUSIVE_BACKGROUND,
            sdk::DISCL_NONEXCLUSIVE | sdk::DISCL_BACKGROUND
        );
        assert_eq!(
            ABSOLUTE_AXIS_ANY_INSTANCE,
            sdk::DIDFT_ABSAXIS | sdk::DIDFT_ANYINSTANCE
        );
        assert_eq!(size_of::<i32>(), size_of::<windows::core::HRESULT>());
    }

    #[repr(C)]
    struct MockInput {
        table: *const *const c_void,
        device: *mut MockDevice,
        enumeration: Vec<(u32, u32)>,
        callback_count: usize,
        created: Vec<GUID>,
        status_guids: Vec<GUID>,
        status_result: i32,
        unexpected: usize,
        released: usize,
    }
    #[repr(C)]
    struct MockDevice {
        table: *const *const c_void,
        calls: Vec<&'static str>,
        format: Option<MockFormat>,
        cooperative: Option<(isize, u32)>,
        state_size: u32,
        poll_result: i32,
        unexpected: usize,
    }
    type MockFormat = (u32, u32, u32, u32, u32, GUID, u32, u32, u32);

    unsafe extern "system" fn mock_enum(
        this: *mut c_void,
        class: u32,
        callback: EnumCallback,
        context: *mut c_void,
        flags: u32,
    ) -> i32 {
        // SAFETY: fixture pointers and callback remain live through this synchronous call.
        unsafe {
            let input = &mut *this.cast::<MockInput>();
            input.enumeration.push((class, flags));
            for i in 1..=64 {
                let mut info = DeviceInstance {
                    size: size_of::<DeviceInstance>() as u32,
                    instance: GUID::from_u128(i),
                    product: GUID::zeroed(),
                    device_type: 0,
                    instance_name: [0; 260],
                    product_name: [0; 260],
                    force_feedback_driver: GUID::zeroed(),
                    usage_page: 1,
                    usage: 4,
                };
                for (out, letter) in info
                    .product_name
                    .iter_mut()
                    .zip("Fake wheel".encode_utf16())
                {
                    *out = letter;
                }
                input.callback_count += 1;
                if callback(&info, context) == 0 {
                    break;
                }
            }
        }
        0
    }
    unsafe extern "system" fn mock_create(
        this: *mut c_void,
        guid: *const GUID,
        output: *mut *mut c_void,
        outer: *mut c_void,
    ) -> i32 {
        // SAFETY: fixture caller supplies valid GUID/output pointers; borrowed mock is not freed.
        unsafe {
            let input = &mut *this.cast::<MockInput>();
            input.created.push(*guid);
            if !outer.is_null() {
                return 0x8007_0057_u32 as i32;
            }
            *output = input.device.cast();
        }
        0
    }
    unsafe extern "system" fn mock_status(this: *mut c_void, guid: *const GUID) -> i32 {
        // SAFETY: correct GetDeviceStatus ABI is exactly this + GUID, with a signed HRESULT.
        unsafe {
            let input = &mut *this.cast::<MockInput>();
            input.status_guids.push(*guid);
            input.status_result
        }
    }
    unsafe extern "system" fn mock_unexpected_input(this: *mut c_void, _: *const GUID) -> i32 {
        // SAFETY: never reached with SDK-verified slots; harmless sentinel if dispatch regresses.
        unsafe {
            (*this.cast::<MockInput>()).unexpected += 1;
        }
        0x8000_4001_u32 as i32
    }
    unsafe extern "system" fn mock_release_input(this: *mut c_void) -> u32 {
        // SAFETY: fixture owns the allocation, so Release records but never frees it.
        unsafe {
            (*this.cast::<MockInput>()).released += 1;
        }
        0
    }
    unsafe extern "system" fn mock_acquire(this: *mut c_void) -> i32 {
        // SAFETY: fixture device is live and mutably worker-owned.
        unsafe {
            (*this.cast::<MockDevice>()).calls.push("Acquire");
        }
        0
    }
    unsafe extern "system" fn mock_unacquire(this: *mut c_void) -> i32 {
        // SAFETY: fixture device is live until the source has dropped.
        unsafe {
            (*this.cast::<MockDevice>()).calls.push("Unacquire");
        }
        0
    }
    unsafe extern "system" fn mock_release_device(this: *mut c_void) -> u32 {
        // SAFETY: fixture owns the allocation, so Release records but never frees it.
        unsafe {
            (*this.cast::<MockDevice>()).calls.push("Release");
        }
        0
    }
    unsafe extern "system" fn mock_poll(this: *mut c_void) -> i32 {
        // SAFETY: fixture device is live and mutably worker-owned.
        unsafe {
            let device = &mut *this.cast::<MockDevice>();
            device.calls.push("Poll");
            device.poll_result
        }
    }
    unsafe extern "system" fn mock_state(
        this: *mut c_void,
        bytes: u32,
        output: *mut c_void,
    ) -> i32 {
        // SAFETY: a four-byte output is only written if the declared buffer size permits it.
        unsafe {
            let device = &mut *this.cast::<MockDevice>();
            device.calls.push("GetDeviceState");
            device.state_size = bytes;
            if bytes != 4 || output.is_null() {
                return 0x8007_0057_u32 as i32;
            }
            *output.cast::<i32>() = 12345;
        }
        0
    }
    unsafe extern "system" fn mock_format(this: *mut c_void, format: *const DataFormat) -> i32 {
        // SAFETY: SetDataFormat call's buffers are live; copy their values before it returns.
        unsafe {
            let device = &mut *this.cast::<MockDevice>();
            device.calls.push("SetDataFormat");
            let format = &*format;
            if format.count != 1 || format.objects.is_null() {
                return 0x8007_0057_u32 as i32;
            }
            let object = &*format.objects;
            device.format = Some((
                format.size,
                format.object_size,
                format.flags,
                format.data_size,
                format.count,
                *object.guid,
                object.offset,
                object.object_type,
                object.flags,
            ));
        }
        0
    }
    unsafe extern "system" fn mock_cooperative(
        this: *mut c_void,
        window: isize,
        flags: u32,
    ) -> i32 {
        // SAFETY: exact SetCooperativeLevel ABI; no real controller is acquired.
        unsafe {
            let device = &mut *this.cast::<MockDevice>();
            device.calls.push("SetCooperativeLevel");
            device.cooperative = Some((window, flags));
        }
        0
    }
    unsafe extern "system" fn mock_unexpected_device(this: *mut c_void) -> i32 {
        // SAFETY: never reached with SDK-verified slots; no UI or FFB API is implemented.
        unsafe {
            (*this.cast::<MockDevice>()).unexpected += 1;
        }
        0x8000_4001_u32 as i32
    }

    #[test]
    fn sdk_abi_dispatch_reads_status_and_selected_axis_without_control_panel_or_output() {
        assert_sdk_abi(); // Fail before calling any fake FFI if a method index/layout changes.
        let mut input_table = [mock_unexpected_input as *const c_void; 11];
        input_table[2] = mock_release_input as *const c_void;
        input_table[3] = mock_create as *const c_void;
        input_table[4] = mock_enum as *const c_void;
        input_table[5] = mock_status as *const c_void;
        // Slot6 remains the unexpected sentinel, never a real RunControlPanel call.
        let mut device_table = [mock_unexpected_device as *const c_void; 32];
        device_table[2] = mock_release_device as *const c_void;
        device_table[7] = mock_acquire as *const c_void;
        device_table[8] = mock_unacquire as *const c_void;
        device_table[9] = mock_state as *const c_void;
        device_table[11] = mock_format as *const c_void;
        device_table[13] = mock_cooperative as *const c_void;
        device_table[25] = mock_poll as *const c_void;
        let mut device = Box::new(MockDevice {
            table: device_table.as_ptr(),
            calls: vec![],
            format: None,
            cooperative: None,
            state_size: 0,
            poll_result: 0,
            unexpected: 0,
        });
        let mut input = Box::new(MockInput {
            table: input_table.as_ptr(),
            device: &mut *device,
            enumeration: vec![],
            callback_count: 0,
            created: vec![],
            status_guids: vec![],
            status_result: 0,
            unexpected: 0,
            released: 0,
        });
        let mut source = DirectInputWheelSource::new();
        source.input = (&mut *input as *mut MockInput).cast(); // Bypasses DirectInput8Create/hardware.
        let devices = source.enumerate().unwrap();
        assert_eq!(devices.len(), 32);
        assert_eq!(input.callback_count, 32);
        source.select(&devices[0].id, 3, 123).unwrap();
        assert_eq!(input.enumeration, vec![(4, 1), (4, 1)]);
        assert_eq!(input.callback_count, 64);
        assert_eq!(input.created, vec![GUID::from_u128(1)]);
        assert_eq!(
            device.format,
            Some((
                size_of::<DataFormat>() as u32,
                size_of::<ObjectFormat>() as u32,
                1,
                4,
                1,
                AXES[3],
                0,
                0x00ff_ff02,
                0
            ))
        );
        assert_eq!(device.cooperative, Some((source.window.0 as isize, 0x0a)));
        assert_ne!(source.window.0 as isize, 123);
        assert_eq!(source.read().axis, Some(12345));
        assert_eq!(source.read().axis, Some(12345));
        assert_eq!(input.status_guids, vec![GUID::from_u128(1)]); // 1Hz presence checks.
        assert_eq!(device.calls.iter().filter(|c| **c == "Acquire").count(), 1);
        assert_eq!(device.state_size, 4);
        input.status_result = 1;
        source.next_presence = Instant::now();
        assert_eq!(source.read().availability, "Not attached");
        input.status_result = 0;
        source.next_presence = Instant::now();
        source.next_acquire = Instant::now();
        device.poll_result = 0x8007_0005_u32 as i32;
        let error = source.read();
        assert_eq!(error.availability, "Read unavailable");
        assert_eq!(error.native_error.as_deref(), Some("0x80070005"));
        let calls = device.calls.len();
        assert_eq!(source.read().availability, "Read unavailable");
        assert_eq!(device.calls.len(), calls); // Reacquire is rate limited after a failed read.
        drop(source);
        assert_eq!(
            &device.calls[device.calls.len() - 2..],
            &["Unacquire", "Release"]
        );
        assert_eq!(input.released, 1);
        assert_eq!(input.unexpected, 0);
        assert_eq!(device.unexpected, 0);
    }

    #[test]
    fn the_format_requests_only_the_selected_axis() {
        for (selected, guid) in AXES.iter().enumerate() {
            let formats = axis_formats(selected);
            assert_eq!(formats.len(), 1);
            let format = &formats[0];
            assert_eq!(format.guid, guid as *const GUID);
            assert_eq!(format.offset, 0);
            assert_eq!(format.object_type, ABSOLUTE_AXIS_ANY_INSTANCE);
            assert_eq!(format.flags, 0);
        }
        assert_eq!(NONEXCLUSIVE_BACKGROUND, 0x0a);
        assert!(validate_selection(6, 123).is_err());
        assert!(validate_selection(0, 0).is_err());
    }

    #[test]
    fn presence_and_read_failure_are_distinct_observations() {
        assert!(presence_reading(0).is_none());
        let absent = presence_reading(1).unwrap();
        assert_eq!(absent.availability, "Not attached");
        assert_eq!(absent.native_error.as_deref(), Some("0x00000001"));
        let failed = presence_reading(0x8007_0005_u32 as i32).unwrap();
        assert_eq!(failed.availability, "Read unavailable");
        assert_eq!(failed.native_error.as_deref(), Some("0x80070005"));
        assert!(failed.axis.is_none());
    }

    #[test]
    fn cooperative_window_is_hidden_and_owned_for_the_source_lifetime() {
        use windows::Win32::UI::WindowsAndMessaging::{IsWindow, IsWindowVisible};
        let mut source = DirectInputWheelSource::new();
        source.ensure_window().unwrap(); // No DirectInput interface or hardware is opened.
        let window = source.window;
        // SAFETY: these read-only queries inspect the source-owned hidden test window.
        unsafe {
            assert!(IsWindow(Some(window)).as_bool());
            assert!(!IsWindowVisible(window).as_bool());
        }
        drop(source);
        // SAFETY: IsWindow accepts an already-destroyed handle and does not dereference it.
        assert!(!unsafe { IsWindow(Some(window)) }.as_bool());
    }
}
