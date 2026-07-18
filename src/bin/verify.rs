use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

pub fn generate_signature(
    api_secret: &str,
    method: &str,
    timestamp: &str,
    path: &str,
    payload_str: &str,
) -> String {
    let signature_data = format!("{}{}{}{}", method, timestamp, path, payload_str);
    let mut mac = HmacSha256::new_from_slice(api_secret.as_bytes())
        .expect("HMAC can take key of any size");
    mac.update(signature_data.as_bytes());
    let result = mac.finalize();
    let code_bytes = result.into_bytes();
    hex::encode(code_bytes)
}

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();

    let api_key = match std::env::var("DELTA_API_KEY") {
        Ok(key) => key,
        Err(_) => {
            eprintln!("Error: DELTA_API_KEY not set in environment or .env file");
            return;
        }
    };
    let api_secret = match std::env::var("DELTA_API_SECRET") {
        Ok(secret) => secret,
        Err(_) => {
            eprintln!("Error: DELTA_API_SECRET not set in environment or .env file");
            return;
        }
    };

    println!("Checking credentials against Delta Exchange GET /v2/wallet/balances...");
    println!("API Key: {}", api_key);
    
    let client = reqwest::Client::builder()
        .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        .build()
        .unwrap_or_else(|_| reqwest::Client::new());

    let timestamp = match SystemTime::now().duration_since(UNIX_EPOCH) {

        Ok(n) => n.as_secs().to_string(),
        Err(e) => {
            eprintln!("Time went backwards: {:?}", e);
            return;
        }
    };

    let method = "GET";
    let path = "/v2/wallet/balances";
    let payload_str = ""; 
    
    let signature = generate_signature(&api_secret, method, &timestamp, path, &payload_str);
    let url = format!("https://api.india.delta.exchange{}", path);

    let res = client
        .get(&url)
        .header("api-key", api_key)
        .header("signature", signature)
        .header("timestamp", timestamp)
        .header("Content-Type", "application/json")
        .send()
        .await;

    match res {
        Ok(response) => {
            let status = response.status();
            let body = response.text().await.unwrap_or_else(|_| "Failed to read body".to_string());
            println!("Status Code: {}", status);
            println!("Response Body: {}", body);
            if status.is_success() {
                println!("\nSUCCESS: API keys are valid and working!");
            } else {
                println!("\nFAILURE: API keys verification failed. Response: {}", body);
            }
        }
        Err(e) => {
            eprintln!("Request error: {:?}", e);
        }
    }
}
