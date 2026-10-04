// Credencial y HTTPS quedan dentro de este ejecutable; Python nunca recibe el token.
import Foundation
import Security
import Darwin

let service = "cl.oveja.erp.toteat.readonly"
let account = "lector-local"
let limit = 2 * 1024 * 1024
enum Failure: Error { case safe(String); case keychain(OSStatus) }
func fail(_ message: String) throws -> Never { throw Failure.safe(message) }
func output(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]), let s = String(data: data, encoding: .utf8) { print(s) }
}
func loginKeychain() throws -> SecKeychain {
    var result: SecKeychain?
    let path = NSHomeDirectory() + "/Library/Keychains/login.keychain-db"
    guard SecKeychainOpen(path, &result) == errSecSuccess, let keychain = result else { try fail("keychain_unavailable") }
    return keychain
}
func keyQuery(_ keychain: SecKeychain) -> [String: Any] {
    return [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: account, kSecMatchSearchList as String: [keychain]]
}
func credentials(allowInteraction: Bool = false) throws -> [String: String] {
    // El llavero de archivo usa también el control de interacción de la API clásica.
    let interactionStatus = SecKeychainSetUserInteractionAllowed(allowInteraction)
    guard interactionStatus == errSecSuccess else { throw Failure.keychain(interactionStatus) }
    let keychain = try loginKeychain()
    var query = keyQuery(keychain)
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    // Un proceso de fondo no debe abrir diálogos ni caer en almacenamiento alternativo.
    query[kSecUseAuthenticationUI as String] = allowInteraction ? kSecUseAuthenticationUIAllow : kSecUseAuthenticationUIFail
    var found: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &found)
    guard status == errSecSuccess else { throw Failure.keychain(status) }
    guard let data = found as? Data,
          let value = try JSONSerialization.jsonObject(with: data) as? [String: String] else { try fail("keychain_access_required") }
    return try validate(value)
}
func validate(_ value: [String: String]) throws -> [String: String] {
    guard Set(value.keys) == Set(["xir", "xil", "xiu", "xapitoken"]) else { try fail("invalid_credentials") }
    for key in ["xir", "xil", "xiu"] {
        guard value[key]!.range(of: "^[A-Za-z0-9_-]{1,80}$", options: .regularExpression) != nil else { try fail("invalid_identifiers") }
    }
    guard let token = value["xapitoken"], !token.isEmpty, token.utf8.count <= 4096,
          !token.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { try fail("invalid_credentials") }
    return value
}
func setup() throws {
    guard isatty(STDIN_FILENO) == 1 else { try fail("use_private_terminal") }
    var value: [String: String] = [:]
    for (key, label) in [("xir", "Restaurante"), ("xil", "Local"), ("xiu", "Usuario API"), ("xapitoken", "Token API")] {
        guard let buffer = getpass("\(label) (oculto): ") else { try fail("input_cancelled") }
        value[key] = String(cString: buffer).trimmingCharacters(in: .whitespacesAndNewlines)
        memset(buffer, 0, strlen(buffer))
    }
    value = try validate(value)
    let keychain = try loginKeychain()
    var app: SecTrustedApplication?
    let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath().path
    guard SecTrustedApplicationCreateFromPath(executable, &app) == errSecSuccess, let trusted = app else { try fail("trusted_helper_unavailable") }
    var access: SecAccess?
    guard SecAccessCreate("ERP Oveja · lector Toteat" as CFString, [trusted] as CFArray, &access) == errSecSuccess,
          let restricted = access else { try fail("restricted_access_unavailable") }
    let data = try JSONSerialization.data(withJSONObject: value)
    // Llavero de archivo local explícito. No se usa Data Protection/iCloud ni acceso para todas las apps.
    let add: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service, kSecAttrAccount as String: account,
        kSecAttrLabel as String: "ERP Oveja · lectura Toteat", kSecValueData as String: data,
        kSecUseKeychain as String: keychain, kSecAttrAccess as String: restricted]
    let status = SecItemAdd(add as CFDictionary, nil)
    if status == errSecDuplicateItem {
        guard SecItemUpdate(keyQuery(keychain) as CFDictionary,
                            [kSecValueData as String: data, kSecAttrAccess as String: restricted] as CFDictionary) == errSecSuccess else { try fail("keychain_update_failed") }
    } else if status != errSecSuccess { try fail("keychain_save_failed") }
    output(["ok": true, "storage": "local_login_keychain", "token_exported": false])
}
func verifiedAccess(_ result: [String: Any], status: Int32) -> Bool {
    return status == 0 && (result["ok"] as? Bool) == true &&
        (result["access_verified"] as? Bool) == true && (result["token_exported"] as? Bool) == false
}
func authorizeVersion() throws {
    // La autorización permanente la concede macOS por decisión del usuario.
    // No reescribe ACLs ni listas de particiones; no confunde acceso puntual con persistente.
    _ = try credentials(allowInteraction: true)
    let process = Process(), pipe = Pipe(), finished = DispatchSemaphore(value: 0)
    process.executableURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    process.arguments = ["access-check"]
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
    process.terminationHandler = { _ in finished.signal() }
    try process.run()
    guard finished.wait(timeout: .now() + 8) == .success else {
        if process.isRunning { process.terminate() }
        try fail("persistent_access_check_timeout")
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    guard data.count <= 4096,
          let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { try fail("persistent_access_check_invalid") }
    guard verifiedAccess(result, status: process.terminationStatus) else {
        var error: [String: Any] = ["ok": false, "error": "persistent_keychain_access_required", "token_exported": false]
        if let status = result["osstatus"] as? Int { error["osstatus"] = status }
        output(error); exit(1)
    }
    output(["ok": true, "version_authorized": true, "persistent_access_verified": true, "token_exported": false])
}
func parsedSalesDay(_ input: String?) throws -> (String, Date) {
    guard let day = input, day.range(of: "^[0-9]{8}$", options: .regularExpression) != nil else { try fail("invalid_sales_day") }
    let format = DateFormatter()
    format.locale = Locale(identifier: "en_US_POSIX")
    format.calendar = Calendar(identifier: .gregorian)
    format.timeZone = TimeZone(secondsFromGMT: 0)
    format.dateFormat = "yyyyMMdd"; format.isLenient = false
    guard let date = format.date(from: day), format.string(from: date) == day else { try fail("invalid_sales_day") }
    return (day, date)
}
func salesDay(_ input: String?) throws -> String { return try parsedSalesDay(input).0 }
func readParameters(mode: String, orderID: String? = nil, endDay: String? = nil) throws -> (String, [String: String]) {
    var parameters: [String: String]
    var endpoint = "orderstatus"
    if mode == "fetch" { parameters = ["listing": "true", "det": "true"] }
    else if mode == "tables" { endpoint = "tables"; parameters = [:] }
    else if mode == "sales-one-day" {
        let day = try salesDay(orderID)
        endpoint = "sales"; parameters = ["ini": day, "end": day]
    }
    else if mode == "sales-range" {
        let start = try parsedSalesDay(orderID), end = try parsedSalesDay(endDay)
        let interval = end.1.timeIntervalSince(start.1)
        guard interval >= 0 else { try fail("sales_range_reversed") }
        // Diagnóstico acotado a una fecha o dos fechas consecutivas, no un barrido histórico.
        guard interval <= 86400 else { try fail("sales_range_exceeds_two_days") }
        endpoint = "sales"; parameters = ["ini": start.0, "end": end.0]
    }
    else {
        guard let id = orderID, id.range(of: "^[0-9]{1,30}$", options: .regularExpression) != nil else { try fail("invalid_order_identifier") }
        switch mode {
        case "detail": parameters = ["ic": id, "det": "true"]
        case "all-information": parameters = ["ic": id, "body_detail_type": "ALL_INFORMATION"]
        case "delivery-information": parameters = ["ic": id, "body_detail_type": "DELIVERY_INFORMATION"]
        default: try fail("read_mode_not_allowed")
        }
    }
    return (endpoint, parameters)
}
func request(_ value: [String: String], mode: String = "fetch", orderID: String? = nil, endDay: String? = nil) throws -> URLRequest {
    let valid = try validate(value)
    let (endpoint, parameters) = try readParameters(mode: mode, orderID: orderID, endDay: endDay)
    var url = URLComponents(string: "https://api.toteat.com/mw/or/1.0/" + endpoint)!
    url.queryItems = parameters.merging(valid) { _, new in new }.sorted(by: {$0.key < $1.key}).map { URLQueryItem(name: $0.key, value: $0.value) }
    var request = URLRequest(url: url.url!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
    request.httpMethod = "GET"; request.setValue("application/json", forHTTPHeaderField: "Accept")
    return request
}
final class Fetch: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    var body = Data(); var response: HTTPURLResponse?; var failure: String?; let done = DispatchSemaphore(value: 0)
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { failure = "redirect_blocked"; completionHandler(nil) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        self.response = response as? HTTPURLResponse
        if response.expectedContentLength > limit { failure = "response_too_large"; completionHandler(.cancel) } else { completionHandler(.allow) }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if body.count + data.count > limit { failure = "response_too_large"; dataTask.cancel() } else { body.append(data) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) { if error != nil && failure == nil { failure = "network_error" }; done.signal() }
}
func reflects(_ value: Any, _ token: String) -> Bool {
    if let s = value as? String { return s.contains(token) }
    if let d = value as? [String: Any] { return d.contains { $0.key.contains(token) || reflects($0.value, token) } }
    if let a = value as? [Any] { return a.contains { reflects($0, token) } }
    return false
}
func fetch(mode: String = "fetch", orderID: String? = nil, endDay: String? = nil) throws {
    _ = try readParameters(mode: mode, orderID: orderID, endDay: endDay)
    guard isatty(STDOUT_FILENO) == 0 else { try fail("internal_pipe_only") }
    let value = try credentials(), delegate = Fetch()
    let config = URLSessionConfiguration.ephemeral
    config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
    config.timeoutIntervalForResource = 20
    let session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    defer { session.invalidateAndCancel() }
    session.dataTask(with: try request(value, mode: mode, orderID: orderID, endDay: endDay)).resume()
    guard delegate.done.wait(timeout: .now() + 22) == .success else { try fail("timeout") }
    if let failure = delegate.failure { try fail(failure) }
    guard let response = delegate.response else { try fail("missing_response") }
    guard response.statusCode == 200 else {
        output(["ok": false, "error": "http_error", "http_status": response.statusCode,
                "retry_after": min(3600, max(60, Int(response.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 60))]); return
    }
    let payload = try JSONSerialization.jsonObject(with: delegate.body)
    guard !reflects(payload, value["xapitoken"]!) else { try fail("credential_reflection_blocked") }
    output(["ok": true, "scope": ["restaurant_id": value["xir"]!, "local_id": value["xil"]!], "payload": payload])
}
do {
    switch CommandLine.arguments.dropFirst().first {
    case "setup": try setup()
    case "fetch", "tables":
        guard CommandLine.arguments.count == 2 else { try fail("invalid_arguments") }
        try fetch(mode: CommandLine.arguments[1])
    case "detail", "all-information", "delivery-information", "sales-one-day":
        guard CommandLine.arguments.count == 3 else { try fail("invalid_arguments") }
        try fetch(mode: CommandLine.arguments[1], orderID: CommandLine.arguments[2])
    case "sales-range":
        guard CommandLine.arguments.count == 4 else { try fail("invalid_arguments") }
        try fetch(mode: "sales-range", orderID: CommandLine.arguments[2], endDay: CommandLine.arguments[3])
    case "authorize-version": try authorizeVersion()
    case "access-check":
        _ = try credentials()
        output(["ok": true, "access_verified": true, "token_exported": false])
    case "self-test":
        let r = try request(["xir": "DEMO", "xil": "DEMO", "xiu": "DEMO", "xapitoken": "FICTICIO"])
        guard r.httpMethod == "GET", r.url?.host == "api.toteat.com", r.url?.path == "/mw/or/1.0/orderstatus",
              reflects(["nested": ["FICTICIO"]], "FICTICIO"), !reflects(["ok": true], "FICTICIO") else { try fail("self_test_failed") }
        for mode in ["detail", "all-information", "delivery-information"] {
            let detail = try request(["xir": "DEMO", "xil": "DEMO", "xiu": "DEMO", "xapitoken": "FICTICIO"], mode: mode, orderID: "9007199254740999")
            let query = URLComponents(url: detail.url!, resolvingAgainstBaseURL: false)!.queryItems!
            guard detail.httpMethod == "GET", detail.url?.host == r.url?.host, detail.url?.path == r.url?.path,
                  query.contains(where: { $0.name == "ic" && $0.value == "9007199254740999" }),
                  !query.contains(where: { $0.name == "listing" }),
                  (mode == "detail" || !query.contains(where: { $0.name == "det" })) else { try fail("detail_self_test_failed") }
        }
        let sales = try request(["xir": "DEMO", "xil": "DEMO", "xiu": "DEMO", "xapitoken": "FICTICIO"], mode: "sales-one-day", orderID: "20261004")
        let query = URLComponents(url: sales.url!, resolvingAgainstBaseURL: false)!.queryItems!
        guard sales.httpMethod == "GET", sales.url?.host == r.url?.host, sales.url?.path == "/mw/or/1.0/sales",
              Set(query.map { $0.name }) == Set(["ini", "end", "xir", "xil", "xiu", "xapitoken"]),
              query.filter({ $0.name == "ini" || $0.name == "end" }).allSatisfy({ $0.value == "20261004" }) else { try fail("sales_self_test_failed") }
        for invalid in ["20260230", "202610", "20261004&end=20261005", "https://example.invalid", "2026-10-04"] {
            do { _ = try salesDay(invalid); try fail("sales_validation_self_test_failed") }
            catch Failure.safe(let code) { if code != "invalid_sales_day" { throw Failure.safe(code) } }
        }
        let demo = ["xir": "DEMO", "xil": "DEMO", "xiu": "DEMO", "xapitoken": "FICTICIO"]
        let tables = try request(demo, mode: "tables")
        let tableQuery = URLComponents(url: tables.url!, resolvingAgainstBaseURL: false)!.queryItems!
        guard tables.httpMethod == "GET", tables.url?.host == "api.toteat.com", tables.url?.scheme == "https",
              tables.url?.path == "/mw/or/1.0/tables", Set(tableQuery.map { $0.name }) == Set(demo.keys) else { try fail("tables_self_test_failed") }
        for (start, end) in [("20261003", "20261004"), ("20261004", "20261004"), ("20240228", "20240229"), ("20261031", "20261101")] {
            let range = try request(demo, mode: "sales-range", orderID: start, endDay: end)
            let rangeQuery = URLComponents(url: range.url!, resolvingAgainstBaseURL: false)!.queryItems!
            guard range.httpMethod == "GET", range.url?.scheme == "https", range.url?.host == "api.toteat.com",
                  range.url?.path == "/mw/or/1.0/sales", range.url?.user == nil, range.url?.fragment == nil,
                  Set(rangeQuery.map { $0.name }) == Set(["ini", "end", "xir", "xil", "xiu", "xapitoken"]),
                  rangeQuery.contains(where: { $0.name == "ini" && $0.value == start }),
                  rangeQuery.contains(where: { $0.name == "end" && $0.value == end }) else { try fail("sales_range_self_test_failed") }
        }
        for (start, end, expected) in [("20261004", "20261003", "sales_range_reversed"), ("20261002", "20261004", "sales_range_exceeds_two_days"), ("20260229", "20260301", "invalid_sales_day"), ("20261003", "20261004&xil=OTHER", "invalid_sales_day")] {
            do { _ = try readParameters(mode: "sales-range", orderID: start, endDay: end); try fail("range_validation_self_test_failed") }
            catch Failure.safe(let code) { if code != expected { throw Failure.safe(code) } }
        }
        for mode in ["https://example.invalid", "../tables", "orders", "DELETE", "shiftstatus"] {
            do { _ = try readParameters(mode: mode, orderID: "1"); try fail("allowlist_self_test_failed") }
            catch Failure.safe(let code) { if code != "read_mode_not_allowed" { throw Failure.safe(code) } }
        }
        guard verifiedAccess(["ok": true, "access_verified": true, "token_exported": false], status: 0),
              !verifiedAccess(["ok": true, "version_authorized": true, "token_exported": false], status: 0),
              !verifiedAccess(["ok": false, "error": "keychain_access_required", "osstatus": -25308], status: 1),
              !verifiedAccess(["ok": true, "access_verified": true, "token_exported": true], status: 0),
              !verifiedAccess(["ok": true, "access_verified": true, "token_exported": false], status: 1) else { try fail("persistent_access_self_test_failed") }
        output(["ok": true, "self_test": true, "network": false, "keychain_access": false])
    default: try fail("use_connect_toteat_script")
    }
} catch Failure.keychain(let status) { output(["ok": false, "error": "keychain_access_required", "osstatus": Int(status)]); exit(1) }
catch Failure.safe(let code) { output(["ok": false, "error": code]); exit(1) }
catch { output(["ok": false, "error": "operation_failed_no_private_details"]); exit(1) }
