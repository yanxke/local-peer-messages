package com.example.local_peer_messages

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val bluetoothChannel = "local_peer_messages/permissions"
    private val bluetoothRequestCode = 4101
    private var pendingPermissionResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, bluetoothChannel)
            .setMethodCallHandler { call, result ->
                if (call.method != "requestBluetoothPermissions") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }

                val missing = bluetoothPermissions().filter {
                    checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
                }
                if (missing.isEmpty()) {
                    result.success(true)
                } else if (pendingPermissionResult != null) {
                    result.success(false)
                } else {
                    pendingPermissionResult = result
                    requestPermissions(missing.toTypedArray(), bluetoothRequestCode)
                }
            }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == bluetoothRequestCode) {
            pendingPermissionResult?.success(
                grantResults.isNotEmpty() && grantResults.all {
                    it == PackageManager.PERMISSION_GRANTED
                },
            )
            pendingPermissionResult = null
        }
    }

    private fun bluetoothPermissions(): List<String> = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        listOf(
            Manifest.permission.BLUETOOTH_SCAN,
            Manifest.permission.BLUETOOTH_ADVERTISE,
            Manifest.permission.BLUETOOTH_CONNECT,
        )
    } else {
        listOf(Manifest.permission.ACCESS_FINE_LOCATION)
    }
}
