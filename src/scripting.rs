use rhai::{Dynamic, Engine, Scope, AST};
use tracing::info;

pub struct VaelenScriptEngine {
    engine: Engine,
    ast: AST,
}

#[derive(Clone)]
pub struct ScriptContext {
    pub price: f64,
    pub size: f64,
    pub is_buy: bool,
    pub now_secs: u64,

    // Extracted fast-path variables
    pub current_cvd: f64,
    pub p95_vol: f64,
    pub atr: f64,
    pub rolling_volume: f64,
    pub past_price: f64,
    pub past_cvd: f64,
    pub ticks_since_entry: u64,

    // Outputs from the script
    pub should_buy: bool,
    pub should_sell: bool,
}

impl VaelenScriptEngine {
    pub fn new(script_path: &str) -> anyhow::Result<Self> {
        // Allowlist: scripts must live under strategies/ to avoid path traversal.
        if !std::path::Path::new(script_path).starts_with("strategies") {
            anyhow::bail!("script_path must live under strategies/: {}", script_path);
        }
        let mut engine = Engine::new();
        engine.set_max_operations(500);
        engine.set_max_string_size(512);
        engine.set_max_array_size(16);
        engine.disable_symbol("eval");
        engine.disable_symbol("import");

        // Register the ScriptContext type and getters
        engine
            .register_type_with_name::<ScriptContext>("ScriptContext")
            .register_get("price", |ctx: &mut ScriptContext| ctx.price)
            .register_get("size", |ctx: &mut ScriptContext| ctx.size)
            .register_get("is_buy", |ctx: &mut ScriptContext| ctx.is_buy)
            .register_get("current_cvd", |ctx: &mut ScriptContext| ctx.current_cvd)
            .register_get("p95_vol", |ctx: &mut ScriptContext| ctx.p95_vol)
            .register_get("atr", |ctx: &mut ScriptContext| ctx.atr)
            .register_get("rolling_volume", |ctx: &mut ScriptContext| {
                ctx.rolling_volume
            })
            .register_get("past_price", |ctx: &mut ScriptContext| ctx.past_price)
            .register_get("past_cvd", |ctx: &mut ScriptContext| ctx.past_cvd)
            .register_get("ticks_since_entry", |ctx: &mut ScriptContext| {
                ctx.ticks_since_entry as i64
            })
            .register_fn("buy", |ctx: &mut ScriptContext| {
                ctx.should_buy = true;
            })
            .register_fn("sell", |ctx: &mut ScriptContext| {
                ctx.should_sell = true;
            });

        let ast = engine
            .compile_file(script_path.into())
            .map_err(|e| anyhow::anyhow!("Rhai compile error: {}", e))?;
        // Log content hash so live vs backtest drift is detectable.
        let digest = std::fs::read(script_path)
            .map(|b| {
                use std::collections::hash_map::DefaultHasher;
                use std::hash::{Hash, Hasher};
                let mut h = DefaultHasher::new();
                b.hash(&mut h);
                h.finish()
            })
            .unwrap_or(0);
        info!(
            "Compiled strategy script: {} (hash {:016x})",
            script_path, digest
        );

        Ok(Self { engine, ast })
    }

    pub fn execute_on_tick(&self, ctx: &mut ScriptContext) -> anyhow::Result<()> {
        // Shared so `ctx.buy()` / `ctx.sell()` inside the script mutate the same
        // cell we read back (a plain by-value argument would be a throwaway copy).
        let shared = Dynamic::from(ctx.clone()).into_shared();
        let mut scope = Scope::new();
        let _ = self
            .engine
            .call_fn::<Dynamic>(&mut scope, &self.ast, "on_tick", (shared.clone(),))
            .map_err(|e| anyhow::anyhow!("Rhai exec error: {}", e))?;

        let updated = shared
            .try_cast::<ScriptContext>()
            .ok_or_else(|| anyhow::anyhow!("Rhai script corrupted its context"))?;
        ctx.should_buy = updated.should_buy;
        ctx.should_sell = updated.should_sell;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> ScriptContext {
        ScriptContext {
            price: 100.0,
            size: 50.0,
            is_buy: true,
            now_secs: 0,
            current_cvd: 20_000.0,
            p95_vol: 10.0,
            atr: 1.0,
            rolling_volume: 20_000.0,
            past_price: 100.5,
            past_cvd: 10_000.0,
            ticks_since_entry: 10_000,
            should_buy: false,
            should_sell: false,
        }
    }

    #[test]
    fn rejects_scripts_outside_strategies_dir() {
        assert!(VaelenScriptEngine::new("../evil.rhai").is_err());
        assert!(VaelenScriptEngine::new("/etc/passwd").is_err());
    }

    #[test]
    fn iceberg_script_sells_into_hidden_buy_absorption() {
        // CVD rising but price falling on a volume spike => sell.
        let engine = VaelenScriptEngine::new("strategies/iceberg_fade.rhai").unwrap();
        let mut c = ctx();
        engine.execute_on_tick(&mut c).unwrap();
        assert!(c.should_sell && !c.should_buy);
    }

    #[test]
    fn iceberg_script_stays_flat_inside_cooldown() {
        let engine = VaelenScriptEngine::new("strategies/iceberg_fade.rhai").unwrap();
        let mut c = ctx();
        c.ticks_since_entry = 10;
        engine.execute_on_tick(&mut c).unwrap();
        assert!(!c.should_sell && !c.should_buy);
    }
}
