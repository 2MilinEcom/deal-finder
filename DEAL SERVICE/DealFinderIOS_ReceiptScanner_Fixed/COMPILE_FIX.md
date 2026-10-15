# Compile Fix

The current Xcode errors are caused by `ObservableObject` and `@Published`
being used without importing their defining framework, Combine.

Replace these three files:

- Services/LocationManager.swift
- ViewModels/ArrivalViewModel.swift
- ViewModels/ReceiptScannerViewModel.swift

Then use Product → Clean Build Folder and build again with Command-B.
