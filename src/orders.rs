use hmac::{Hmac, Mac};
use reqwest::{header, Client, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;

type HmacSha256 = Hmac<Sha256>;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct OrderRequest {
    pub product_id: u64,
    pub size: i64,
    pub side: String,
    pub order_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub post_only: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub client_order_id: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct OrderResponse {
    pub id: String,
    pub product_id: u64,
    pub size: i64,
    pub side: String,
    pub order_type: String,
    pub status: String,
    pub filled_size: Option<i64>,
    pub avg_fill_price: Option<String>,
}

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

#[derive(Error, Debug)]
pub enum OrderError {
    #[error("Request failed: {0}")]
    RequestFailed(#[from] reqwest::Error),
    #[error("Serialization failed: {0}")]
    SerializationFailed(#[from] serde_json::Error),
    #[error("API error: {status} - {body}")]
    ApiError { status: StatusCode, body: String },
    #[error("Timeout after {0} seconds")]
    Timeout(u64),
    #[error("Max retries exceeded")]
    MaxRetriesExceeded,
    #[error("Fill confirmation timeout")]
    FillTimeout,
    #[error("Circuit breaker is open due to consecutive failures. Manual intervention required.")]
    CircuitBreakerOpen,
}

#[derive(Debug, Clone)]
pub struct FillInfo {
    pub order_id: String,
    pub price: f64,
    pub filled_size: i64,
    pub status: String,
}

#[derive(Debug)]
pub enum OrderResult {
    Filled(FillInfo),
    Partial(FillInfo),
    Open(FillInfo),
    Failed(OrderError),
}

pub struct OrderManager {
    client: Arc<Client>,
    api_key: String,
    api_secret: String,
    api_base_url: String,
    max_consecutive_failures: u32,
    consecutive_failures: AtomicU32,
    pub circuit_open: tokio::sync::RwLock<bool>,
}

impl OrderManager {
    pub fn new(
        api_key: String,
        api_secret: String,
        api_base_url: String,
        request_timeout: Duration,
        max_consecutive_failures: u32,
    ) -> Self {
        let client = Client::builder()
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            .tcp_keepalive(Duration::from_secs(30))
            .pool_idle_timeout(None)
            .timeout(request_timeout)
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            client: Arc::new(client),
            api_key,
            api_secret,
            api_base_url,
            max_consecutive_failures,
            consecutive_failures: AtomicU32::new(0),
            circuit_open: tokio::sync::RwLock::new(false),
        }
    }

    pub async fn reset_circuit_breaker(&self) {
        *self.circuit_open.write().await = false;
        self.consecutive_failures.store(0, Ordering::SeqCst);
        tracing::info!("Circuit breaker manually reset.");
    }

    pub async fn place_order(
        &self,
        product_id: u64,
        price: Option<f64>,
        size: i64,
        side: &str,
        order_type: &str,
        client_order_id: Option<String>,
    ) -> Result<OrderResult, OrderError> {
        if *self.circuit_open.read().await {
            return Err(OrderError::CircuitBreakerOpen);
        }

        use std::time::{SystemTime, UNIX_EPOCH};

        let order_req = OrderRequest {
            product_id,
            size,
            side: side.to_string(),
            order_type: order_type.to_string(),
            price: price.map(|p| p.to_string()),
            post_only: if order_type == "limit" { Some("true".to_string()) } else { None },
            client_order_id,
        };

        let payload_str = serde_json::to_string(&order_req)?;

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| OrderError::Timeout(0))?
            .as_secs()
            .to_string();

        let method = "POST";
        let path = "/v2/orders";
        let signature = generate_signature(&self.api_secret, method, &timestamp, path, &payload_str);

        let url = format!("{}{}", self.api_base_url, path);

        let response = self.client
            .post(&url)
            .header("api-key", &self.api_key)
            .header("signature", signature)
            .header("timestamp", timestamp)
            .header(header::CONTENT_TYPE, "application/json")
            .body(payload_str)
            .send()
            .await?;

        let status = response.status();
        let body = response.text().await?;

        if status.is_success() {
            let order_resp: OrderResponse = serde_json::from_str(&body)?;
            let fill_price = order_resp.avg_fill_price.unwrap_or("0".to_string()).parse().unwrap_or(0.0);
            let fill_info = FillInfo {
                order_id: order_resp.id,
                price: fill_price,
                filled_size: order_resp.filled_size.unwrap_or(0),
                status: order_resp.status.clone(),
            };
            match order_resp.status.as_str() {
                "open" | "pending" => Ok(OrderResult::Open(fill_info)),
                "partial_fill" | "partially_filled" => Ok(OrderResult::Partial(fill_info)),
                _ => Ok(OrderResult::Filled(fill_info)),
            }
        } else {
            Err(OrderError::ApiError { status, body })
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn place_order_with_retry(
        &self,
        product_id: u64,
        price: Option<f64>,
        size: i64,
        side: &str,
        order_type: &str,
        max_retries: u32,
        base_delay_secs: u64,
        max_delay_secs: u64,
        retry_on_status: &[u16],
    ) -> OrderResult {
        if *self.circuit_open.read().await {
            return OrderResult::Failed(OrderError::CircuitBreakerOpen);
        }

        let mut attempt = 0;
        let client_order_id = Some(uuid::Uuid::new_v4().simple().to_string());

        loop {
            match self.place_order(product_id, price, size, side, order_type, client_order_id.clone()).await {
                Ok(result) => {
                    self.consecutive_failures.store(0, Ordering::SeqCst);
                    return result;
                }
                Err(e) => {
                    let mut is_duplicate = false;
                    if let OrderError::ApiError { status, body } = &e {
                        if status == &reqwest::StatusCode::BAD_REQUEST {
                            let body_lower = body.to_lowercase();
                            if (body_lower.contains("duplicate") || body_lower.contains("already exists")) 
                                && body_lower.contains("client_order_id") 
                            {
                                is_duplicate = true;
                            }
                        }
                    }

                    if is_duplicate {
                        tracing::info!("Duplicate client_order_id detected. Reconciling via GET...");
                        if let Some(cid) = &client_order_id {
                            match self.get_order_status_by_client_id(cid).await {
                                Ok(order_resp) => {
                                    self.consecutive_failures.store(0, Ordering::SeqCst);
                                    let fill_price = order_resp.avg_fill_price.unwrap_or("0".to_string()).parse().unwrap_or(0.0);
                                    let fill_info = FillInfo {
                                        order_id: order_resp.id,
                                        price: fill_price,
                                        filled_size: order_resp.filled_size.unwrap_or(0),
                                        status: order_resp.status.clone(),
                                    };
                                    return match order_resp.status.as_str() {
                                        "open" | "pending" => OrderResult::Open(fill_info),
                                        "partial_fill" | "partially_filled" => OrderResult::Partial(fill_info),
                                        "cancelled" | "rejected" => OrderResult::Failed(OrderError::ApiError { status: reqwest::StatusCode::GONE, body: "Order cancelled".to_string() }),
                                        _ => OrderResult::Filled(fill_info),
                                    };
                                }
                                Err(get_err) => {
                                    tracing::warn!("Failed to reconcile duplicate client_order_id state: {:?}", get_err);
                                    // Let it fall through to failure increment
                                }
                            }
                        }
                    }

                    let should_retry = match &e {
                        OrderError::ApiError { status, .. } => {
                            retry_on_status.contains(&status.as_u16())
                        }
                        OrderError::RequestFailed(e) if e.is_timeout() || e.is_connect() => true,
                        OrderError::Timeout(_) => true,
                        _ => false,
                    };

                    if !should_retry || attempt >= max_retries {
                        let fails = self.consecutive_failures.fetch_add(1, Ordering::SeqCst) + 1;
                        if fails >= self.max_consecutive_failures {
                            *self.circuit_open.write().await = true;
                            tracing::error!("Circuit breaker tripped after {} consecutive failures. Manual intervention required.", fails);
                        }
                        return OrderResult::Failed(e);
                    }

                    let delay = std::cmp::min(
                        base_delay_secs * 2u64.pow(attempt),
                        max_delay_secs
                    );

                    tracing::warn!(
                        "Order failed (attempt {}/{}): {:?}. Retrying in {}s...",
                        attempt + 1,
                        max_retries + 1,
                        e,
                        delay
                    );

                    tokio::time::sleep(Duration::from_secs(delay)).await;
                    attempt += 1;
                }
            }
        }
    }

    pub async fn cancel_order(
        &self,
        product_id: u64,
        order_id: &str,
    ) -> Result<(), OrderError> {
        use std::time::{SystemTime, UNIX_EPOCH};

        #[derive(serde::Serialize)]
        struct CancelRequest {
            product_id: u64,
            id: String,
        }
        let cancel_req = CancelRequest {
            product_id,
            id: order_id.to_string(),
        };

        let payload_str = serde_json::to_string(&cancel_req)?;

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| OrderError::Timeout(0))?
            .as_secs()
            .to_string();

        let method = "DELETE";
        let path = "/v2/orders";
        let signature = generate_signature(&self.api_secret, method, &timestamp, path, &payload_str);
        let url = format!("{}{}", self.api_base_url, path);

        let response = self.client
            .delete(&url)
            .header("api-key", &self.api_key)
            .header("signature", signature)
            .header("timestamp", timestamp)
            .header(header::CONTENT_TYPE, "application/json")
            .body(payload_str)
            .send()
            .await?;

        let status = response.status();
        if status.is_success() {
            Ok(())
        } else {
            let body = response.text().await.unwrap_or_default();
            Err(OrderError::ApiError { status, body })
        }
    }

    pub async fn get_order_status_raw(
        &self,
        order_id: &str,
    ) -> Result<OrderResponse, OrderError> {
        use std::time::{SystemTime, UNIX_EPOCH};

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| OrderError::Timeout(0))?
            .as_secs()
            .to_string();

        let method = "GET";
        let path = format!("/v2/orders/{}", order_id);
        let payload_str = "";
        let signature = generate_signature(&self.api_secret, method, &timestamp, &path, payload_str);

        let url = format!("{}{}", self.api_base_url, path);

        let response = self.client
            .get(&url)
            .header("api-key", &self.api_key)
            .header("signature", signature)
            .header("timestamp", timestamp)
            .send()
            .await?;

        let status = response.status();
        let body = response.text().await?;

        if status.is_success() {
            Ok(serde_json::from_str(&body)?)
        } else {
            Err(OrderError::ApiError { status, body })
        }
    }

    pub async fn get_order_status_by_client_id(
        &self,
        client_order_id: &str,
    ) -> Result<OrderResponse, OrderError> {
        use std::time::{SystemTime, UNIX_EPOCH};

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| OrderError::Timeout(0))?
            .as_secs()
            .to_string();

        let method = "GET";
        let path = format!("/v2/orders/client_order_id/{}", client_order_id);
        let payload_str = "";
        let signature = generate_signature(&self.api_secret, method, &timestamp, &path, payload_str);

        let url = format!("{}{}", self.api_base_url, path);

        let response = self.client
            .get(&url)
            .header("api-key", &self.api_key)
            .header("signature", signature)
            .header("timestamp", timestamp)
            .send()
            .await?;

        let status = response.status();
        let body = response.text().await?;

        if status.is_success() {
            Ok(serde_json::from_str(&body)?)
        } else {
            Err(OrderError::ApiError { status, body })
        }
    }

    pub async fn get_order_status(
        &self,
        order_id: &str,
    ) -> Result<OrderResult, OrderError> {
        let order_resp = self.get_order_status_raw(order_id).await?;
        let fill_price = order_resp.avg_fill_price.unwrap_or("0".to_string()).parse().unwrap_or(0.0);
        let fill_info = FillInfo {
            order_id: order_resp.id,
            price: fill_price,
            filled_size: order_resp.filled_size.unwrap_or(0),
            status: order_resp.status.clone(),
        };
        match order_resp.status.as_str() {
            "open" | "pending" => Ok(OrderResult::Open(fill_info)),
            "partial_fill" | "partially_filled" => Ok(OrderResult::Partial(fill_info)),
            "cancelled" | "rejected" => Err(OrderError::ApiError { status: reqwest::StatusCode::GONE, body: "Order cancelled".to_string() }),
            _ => Ok(OrderResult::Filled(fill_info)),
        }
    }

    pub async fn wait_for_fill(
        &self,
        order_id: &str,
        timeout_secs: u64,
        poll_interval_ms: u64,
    ) -> Result<FillInfo, OrderError> {
        let start = std::time::Instant::now();
        let timeout = Duration::from_secs(timeout_secs);
        let poll_interval = Duration::from_millis(poll_interval_ms);

        loop {
            if start.elapsed() > timeout {
                return Err(OrderError::FillTimeout);
            }

            match self.get_order_status(order_id).await {
                Ok(OrderResult::Filled(fill)) => return Ok(fill),
                Ok(OrderResult::Partial(fill)) => return Ok(fill),
                Ok(OrderResult::Open(_)) => {},
                Ok(OrderResult::Failed(e)) => return Err(e),
                Err(e) => {
                    if let OrderError::ApiError { status, .. } = &e {
                        if status == &reqwest::StatusCode::GONE {
                            return Err(e);
                        }
                    }
                }
            }

            tokio::time::sleep(poll_interval).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[tokio::test]
    async fn test_circuit_breaker_transitions() {
        let manager = OrderManager::new(
            "key".into(),
            "secret".into(),
            "http://127.0.0.1:65535".into(), // intentionally bad URL
            Duration::from_millis(100),
            3,
        );

        // 1st logical failure
        let r1 = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        assert!(matches!(r1, OrderResult::Failed(_)));
        assert!(!*manager.circuit_open.read().await);

        // 2nd logical failure
        let r2 = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        assert!(!*manager.circuit_open.read().await);

        // 3rd logical failure -> trips CB
        let r3 = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        assert!(*manager.circuit_open.read().await);

        // 4th attempt -> fails fast
        let r4 = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        assert!(matches!(r4, OrderResult::Failed(OrderError::CircuitBreakerOpen)));

        // manual reset
        manager.reset_circuit_breaker().await;
        assert!(!*manager.circuit_open.read().await);
    }

    #[tokio::test]
    async fn test_client_order_id_stability() {
        use tokio::net::TcpListener;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{}", addr);

        let manager = Arc::new(OrderManager::new(
            "key".into(),
            "secret".into(),
            base_url,
            Duration::from_millis(50),
            3,
        ));

        let server_handle = tokio::spawn(async move {
            let mut ids = Vec::new();
            for _ in 0..4 {
                if let Ok((mut socket, _)) = listener.accept().await {
                    let mut buf = [0; 1024];
                    let n = socket.read(&mut buf).await.unwrap_or(0);
                    let req_str = String::from_utf8_lossy(&buf[..n]);
                    
                    if let Some(idx) = req_str.find("\"client_order_id\":\"") {
                        let start = idx + 19;
                        if let Some(end) = req_str[start..].find("\"") {
                            ids.push(req_str[start..start+end].to_string());
                        }
                    }
                    
                    let response = "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n";
                    let _ = socket.write_all(response.as_bytes()).await;
                }
            }
            ids
        });

        // 1st logical order: 1 attempt + 2 retries = 3 requests total
        let _ = manager.place_order_with_retry(1, None, 10, "buy", "market", 2, 0, 0, &[500]).await;
        
        // 2nd logical order: 1 attempt = 1 request
        let _ = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;

        let ids = server_handle.await.unwrap();
        assert_eq!(ids.len(), 4, "Expected 4 parsed client_order_ids");
        
        // Retries of order 1 share the same ID
        assert_eq!(ids[0], ids[1]);
        assert_eq!(ids[1], ids[2]);
        
        // Order 2 gets a NEW distinct ID
        assert_ne!(ids[2], ids[3]);
    }

    #[tokio::test]
    async fn test_duplicate_client_order_id_recovery() {
        use tokio::net::TcpListener;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{}", addr);

        let manager = OrderManager::new(
            "key".into(),
            "secret".into(),
            base_url,
            Duration::from_millis(50),
            3,
        );

        let server_handle = tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // 1st request: simulate failure (e.g. timeout or 500)
                let response = "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n";
                let _ = socket.write_all(response.as_bytes()).await;
            }

            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // 2nd request: retry gets 400 duplicate client_order_id
                let body = r#"{"error":{"message":"duplicate client_order_id already exists"}}"#;
                let response = format!("HTTP/1.1 400 Bad Request\r\nContent-Length: {}\r\n\r\n{}", body.len(), body);
                let _ = socket.write_all(response.as_bytes()).await;
            }

            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // 3rd request: GET /v2/orders/client_order_id/...
                let body = r#"{"id":"order_123","product_id":1,"size":10,"side":"buy","order_type":"market","status":"filled","filled_size":10,"avg_fill_price":"100.5"}"#;
                let response = format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{}", body.len(), body);
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });

        // We retry on 500, but not on 400
        let result = manager.place_order_with_retry(1, None, 10, "buy", "market", 2, 0, 0, &[500]).await;
        
        let _ = server_handle.await;

        match result {
            OrderResult::Filled(fill) => {
                assert_eq!(fill.order_id, "order_123");
                assert_eq!(fill.filled_size, 10);
                assert_eq!(fill.price, 100.5);
            }
            _ => panic!("Expected Filled result"),
        }
        
        // Circuit breaker should NOT be open because it successfully reconciled.
        assert!(!*manager.circuit_open.read().await);
        assert_eq!(manager.consecutive_failures.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn test_genuine_400_error_trips_cb() {
        use tokio::net::TcpListener;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{}", addr);

        let manager = OrderManager::new(
            "key".into(),
            "secret".into(),
            base_url,
            Duration::from_millis(50),
            1, // trip on 1 failure
        );

        let server_handle = tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // Genuine 400 error not related to duplicate
                let body = r#"{"error":{"message":"invalid product id"}}"#;
                let response = format!("HTTP/1.1 400 Bad Request\r\nContent-Length: {}\r\n\r\n{}", body.len(), body);
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });

        let result = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        let _ = server_handle.await;

        assert!(matches!(result, OrderResult::Failed(OrderError::ApiError{..})));
        assert!(*manager.circuit_open.read().await);
    }

    #[tokio::test]
    async fn test_duplicate_recovery_get_fails_degrades_gracefully() {
        use tokio::net::TcpListener;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{}", addr);

        let manager = OrderManager::new(
            "key".into(),
            "secret".into(),
            base_url,
            Duration::from_millis(50),
            1, // trip on 1 failure
        );

        let server_handle = tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // 1st request: duplicate client_order_id
                let body = r#"{"error":{"message":"duplicate client_order_id"}}"#;
                let response = format!("HTTP/1.1 400 Bad Request\r\nContent-Length: {}\r\n\r\n{}", body.len(), body);
                let _ = socket.write_all(response.as_bytes()).await;
            }

            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0; 1024];
                let _ = socket.read(&mut buf).await;
                // 2nd request: GET /v2/orders/client_order_id/...
                // Simulate 500 error on GET
                let response = "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n";
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });

        let result = manager.place_order_with_retry(1, None, 10, "buy", "market", 0, 0, 0, &[]).await;
        let _ = server_handle.await;

        // Should return the original OrderError or the GET error
        assert!(matches!(result, OrderResult::Failed(_)));
        // Should trip the circuit breaker since we couldn't verify the state
        assert!(*manager.circuit_open.read().await);
    }
}