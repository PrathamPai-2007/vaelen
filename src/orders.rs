use crate::OrderResponse;
use reqwest::{header, Client, StatusCode};
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;

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
}

impl OrderManager {
    pub fn new(
        api_key: String,
        api_secret: String,
        api_base_url: String,
        request_timeout: Duration,
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
        }
    }

    pub async fn place_order(
        &self,
        product_id: u64,
        price: Option<f64>,
        size: i64,
        side: &str,
        order_type: &str,
    ) -> Result<OrderResult, OrderError> {
        use crate::generate_signature;
        use std::time::{SystemTime, UNIX_EPOCH};

        let order_req = crate::OrderRequest {
            product_id,
            size,
            side: side.to_string(),
            order_type: order_type.to_string(),
            price: price.map(|p| p.to_string()),
            post_only: if order_type == "limit" { Some("true".to_string()) } else { None },
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
        let mut attempt = 0;

        loop {
            match self.place_order(product_id, price, size, side, order_type).await {
                Ok(result) => return result,
                Err(e) => {
                    let should_retry = match &e {
                        OrderError::ApiError { status, .. } => {
                            retry_on_status.contains(&status.as_u16())
                        }
                        OrderError::RequestFailed(e) if e.is_timeout() || e.is_connect() => true,
                        OrderError::Timeout(_) => true,
                        _ => false,
                    };

                    if !should_retry || attempt >= max_retries {
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
        use crate::generate_signature;
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
    ) -> Result<crate::OrderResponse, OrderError> {
        use crate::generate_signature;
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
                Ok(OrderResult::Partial(fill)) => return Ok(fill), // Could also wait for full fill depending on logic
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