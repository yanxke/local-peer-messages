import Cocoa
import FlutterMacOS
import CoreBluetooth

final class MainFlutterWindow: NSWindow, CBCentralManagerDelegate {
  private var bluetoothManager: CBCentralManager?
  private var pendingBluetoothPermissionResult: FlutterResult?

  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setFrame(windowFrame, display: true)

    // macOS has no Android-style runtime permission API, but leaving this
    // method channel unregistered can keep the Dart permission Future pending.
    // Create a real CoreBluetooth manager so macOS can present and resolve its
    // system authorization prompt before LPC initializes its own managers.
    let permissionChannel = FlutterMethodChannel(
      name: "local_peer_messages/permissions",
      binaryMessenger: flutterViewController.engine.binaryMessenger)
    permissionChannel.setMethodCallHandler { [weak self] call, result in
      guard call.method == "requestBluetoothPermissions" else {
        result(FlutterMethodNotImplemented)
        return
      }
      self?.requestBluetoothPermission(result)
    }

    RegisterGeneratedPlugins(registry: flutterViewController)

    super.awakeFromNib()
  }

  private func requestBluetoothPermission(_ result: @escaping FlutterResult) {
    guard pendingBluetoothPermissionResult == nil else {
      result(FlutterError(
        code: "PERMISSION_REQUEST_IN_PROGRESS",
        message: "Bluetooth permission request already in progress",
        details: nil))
      return
    }
    pendingBluetoothPermissionResult = result
    if bluetoothManager == nil {
      bluetoothManager = CBCentralManager(delegate: self, queue: .main)
    }
    resolveBluetoothPermissionIfKnown()
  }

  func centralManagerDidUpdateState(_ central: CBCentralManager) {
    resolveBluetoothPermissionIfKnown()
  }

  private func resolveBluetoothPermissionIfKnown() {
    guard let result = pendingBluetoothPermissionResult,
          let central = bluetoothManager else { return }
    if #available(macOS 10.15, *) {
      switch CBCentralManager.authorization {
      case .allowedAlways:
        pendingBluetoothPermissionResult = nil
        result(true)
      case .denied, .restricted:
        pendingBluetoothPermissionResult = nil
        result(false)
      case .notDetermined:
        break
      @unknown default:
        break
      }
    } else if central.state == .poweredOn {
      pendingBluetoothPermissionResult = nil
      result(true)
    } else if central.state == .unauthorized || central.state == .unsupported {
      pendingBluetoothPermissionResult = nil
      result(false)
    }
  }
}
