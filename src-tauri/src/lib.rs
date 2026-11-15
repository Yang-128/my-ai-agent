
use std::{
    net::{TcpStream, SocketAddr},
    process::{Child, Command},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent};

struct BackendProcess(Mutex<Option<Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            } else {
                let data_dir = app.path().app_data_dir()?;
                std::fs::create_dir_all(&data_dir)?;

                let backend = app.path().resource_dir()?.join("_up_").join("dist").join("backend");
                let child = Command::new(backend)
                    .env("MY_AI_AGENT_DATA_DIR", &data_dir)
                    .spawn()
                    .expect("Failed to start My AI Agent backend");

                *app.state::<BackendProcess>().0.lock().unwrap() = Some(child);

                let address: SocketAddr = "127.0.0.1:8000".parse().unwrap();
                let deadline = Instant::now() + Duration::from_secs(30);
                while Instant::now() < deadline {
                    if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
                        break;
                    }
                    thread::sleep(Duration::from_millis(200));
                }

                if let Some(window) = app.get_webview_window("main") {
                    window.eval("window.location.replace('http://127.0.0.1:8000')")?;
                }
            }
            Ok(())
        });

    builder
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<BackendProcess>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}
