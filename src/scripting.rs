use rhai::{Engine, Scope, AST};
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
        let mut engine = Engine::new();
        
        // Register the ScriptContext type and getters
        engine.register_type_with_name::<ScriptContext>("ScriptContext")
            .register_get("price", |ctx: &mut ScriptContext| ctx.price)
            .register_get("size", |ctx: &mut ScriptContext| ctx.size)
            .register_get("is_buy", |ctx: &mut ScriptContext| ctx.is_buy)
            .register_get("current_cvd", |ctx: &mut ScriptContext| ctx.current_cvd)
            .register_get("p95_vol", |ctx: &mut ScriptContext| ctx.p95_vol)
            .register_get("atr", |ctx: &mut ScriptContext| ctx.atr)
            .register_get("rolling_volume", |ctx: &mut ScriptContext| ctx.rolling_volume)
            .register_get("past_price", |ctx: &mut ScriptContext| ctx.past_price)
            .register_get("past_cvd", |ctx: &mut ScriptContext| ctx.past_cvd)
            .register_get("ticks_since_entry", |ctx: &mut ScriptContext| ctx.ticks_since_entry as i64)
            .register_fn("buy", |ctx: &mut ScriptContext| { ctx.should_buy = true; })
            .register_fn("sell", |ctx: &mut ScriptContext| { ctx.should_sell = true; });

        let ast = engine.compile_file(script_path.into())
            .map_err(|e| anyhow::anyhow!("Rhai compile error: {}", e))?;
        info!("Compiled strategy script: {}", script_path);

        Ok(Self { engine, ast })
    }

    pub fn execute_on_tick(
        &self,
        ctx: &mut ScriptContext,
    ) -> anyhow::Result<()> {
        let mut scope = Scope::new();
        scope.push("ctx", ctx.clone());
        
        self.engine.call_fn::<()>(&mut scope, &self.ast, "on_tick", ())
            .map_err(|e| anyhow::anyhow!("Rhai exec error: {}", e))?;
        
        let updated_ctx: ScriptContext = scope.get_value("ctx").unwrap();
        ctx.should_buy = updated_ctx.should_buy;
        ctx.should_sell = updated_ctx.should_sell;
        
        Ok(())
    }
}
