import SwiftUI
import AVFoundation

struct QRScanner: UIViewControllerRepresentable {
    let scanned: (String) -> Void
    func makeUIViewController(context: Context) -> ScannerController { ScannerController(scanned:scanned) }
    func updateUIViewController(_ controller: ScannerController,context: Context) {}
    static func dismantleUIViewController(_ controller: ScannerController,coordinator: ()) { controller.stop() }
}
final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    private let capture = AVCaptureSession()
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.camera")
    private var preview: AVCaptureVideoPreviewLayer?
    private let scanned: (String) -> Void
    private var delivered = false
    private var stopped = false
    init(scanned: @escaping (String) -> Void) { self.scanned = scanned; super.init(nibName:nil,bundle:nil) }
    required init?(coder: NSCoder) { fatalError() }
    override func viewDidLoad() {
        super.viewDidLoad(); view.backgroundColor = .black
        AVCaptureDevice.requestAccess(for:.video) { [weak self] allowed in
            guard let self else { return }
            guard allowed else { DispatchQueue.main.async { self.showError("请在系统设置中允许相机权限") }; return }
            self.queue.async {
                guard !self.stopped else { return }
                do {
                    guard let device = AVCaptureDevice.default(for:.video) else { return }
                    let input = try AVCaptureDeviceInput(device:device), output = AVCaptureMetadataOutput()
                    self.capture.beginConfiguration()
                    guard self.capture.canAddInput(input), self.capture.canAddOutput(output) else { self.capture.commitConfiguration(); return }
                    self.capture.addInput(input); self.capture.addOutput(output); output.setMetadataObjectsDelegate(self,queue:.main); output.metadataObjectTypes = [.qr]; self.capture.commitConfiguration()
                    DispatchQueue.main.async { let layer = AVCaptureVideoPreviewLayer(session:self.capture); layer.videoGravity = .resizeAspectFill; layer.frame = self.view.bounds; self.view.layer.addSublayer(layer); self.preview = layer }
                    self.capture.startRunning()
                } catch { DispatchQueue.main.async { self.showError("相机暂不可用") } }
            }
        }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); preview?.frame = view.bounds }
    func stop() { queue.async { self.stopped = true; self.capture.stopRunning() } }
    private func showError(_ text: String) { let label = UILabel(frame:view.bounds); label.text = text; label.textColor = .white; label.textAlignment = .center; label.autoresizingMask = [.flexibleWidth,.flexibleHeight]; view.addSubview(label) }
    func metadataOutput(_ output: AVCaptureMetadataOutput,didOutput objects: [AVMetadataObject],from connection: AVCaptureConnection) {
        guard !delivered, let value = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
        delivered = true; stop(); scanned(value)
    }
}
