// Eve's native-shell Rust layer. Deliberately thin: file I/O for the shared
// JSON stores, JS-eval into the YouTube webview, the exe-picker dialog, and
// the tray icon. Everything else (window creation, geometry, panels) lives in
// ui/src/eve-tauri-shell.js using the injected __TAURI__ API — porting the
// Electron main.js logic nearly 1:1.

use std::path::PathBuf;
use tauri::{AppHandle, Emitter, Manager};

// ponytail: dev-time repo root baked at compile time; EVE_ROOT env overrides
// for a bundled install (bundle path handling is cutover-phase work).
fn eve_root() -> PathBuf {
    std::env::var("EVE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

fn file_path(key: &str) -> Result<PathBuf, String> {
    // Mirrors the file map at the top of ui/main.js.
    let name = match key {
        "settings" => "settings.json",
        "tiling" => "tiling_layouts.json",
        "commands" => "custom_commands.json",
        "apps" => "apps.json",
        "aliases" => "aliases.json",
        _ => return Err(format!("unknown file key: {key}")),
    };
    Ok(eve_root().join(name))
}

#[tauri::command]
fn file_get(key: String) -> Result<String, String> {
    std::fs::read_to_string(file_path(&key)?).map_err(|e| e.to_string())
}

#[tauri::command]
fn file_set(key: String, text: String) -> Result<(), String> {
    std::fs::write(file_path(&key)?, text).map_err(|e| e.to_string())
}

/// Run JS inside another webview (the YouTube HUD — a remote page with no IPC,
/// so scroll/number/open control must be injected from outside).
#[tauri::command]
fn eval_in(app: AppHandle, label: String, js: String) -> Result<(), String> {
    app.get_webview_window(&label)
        .ok_or_else(|| format!("no window '{label}'"))?
        .eval(&js)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return Err("only http(s) urls".into());
    }
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
async fn browse_exe(app: AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Pick executable")
            .add_filter("Executables", &["exe", "lnk", "bat", "cmd"])
            .add_filter("All files", &["*"])
            .blocking_pick_file()
            .map(|f| f.to_string())
    })
    .await
    .ok()
    .flatten()
}

fn create_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open_dir = MenuItem::with_id(app, "show-directory", "Open Directory", true, None::<&str>)?;
    let wm = MenuItem::with_id(app, "open-window-manager", "Window Manager", true, None::<&str>)?;
    let am = MenuItem::with_id(app, "open-app-manager", "App Manager", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Eve", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open_dir, &wm, &am, &sep, &quit])?;

    TrayIconBuilder::with_id("eve")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Eve")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "quit" => app.exit(0),
            // Menu ids double as eve-cmd channel names; the shell dispatches.
            id => {
                let _ = app.emit("eve-cmd", serde_json::json!({ "ch": id, "args": null }));
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let _ = tray
                    .app_handle()
                    .emit("eve-cmd", serde_json::json!({ "ch": "toggle-directory", "args": null }));
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            file_get,
            file_set,
            eval_in,
            open_external,
            browse_exe
        ])
        .setup(|app| {
            create_tray(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
