use reqwest::Client;
use tokio::sync::mpsc;
use tracing::{error, info};

pub struct TelegramNotifier;

impl TelegramNotifier {
    pub fn spawn(token: String, chat_id: String) -> mpsc::Sender<String> {
        let (tx, mut rx) = mpsc::channel::<String>(100);
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .unwrap_or_else(|_| Client::new());

        tokio::spawn(async move {
            let url = format!("https://api.telegram.org/bot{}/sendMessage", token);
            while let Some(msg) = rx.recv().await {
                let payload = serde_json::json!({
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "HTML"
                });

                match client.post(&url).json(&payload).send().await {
                    Ok(resp) => {
                        if !resp.status().is_success() {
                            error!("Telegram rejected alert: {}", resp.status());
                        }
                    }
                    Err(e) => error!("Failed to send Telegram alert: {}", e),
                }
            }
        });

        info!("Telegram notifications enabled.");
        tx
    }
}
