# PumpFun Python

Trade on pump.fun with smart strategy detection. **UnifiedPumpFun automatically figures out if a token is still in bonding curve or has graduated to PumpSwap DEX** - no need to check manually.

## Usage

```bash
python3 -m venv venv
source venv/bin/activate  
pip install -r requirements.txt
```

Add your private key and RPC to `.env`:

```python
import asyncio
from model.pump_fun.unified_pump_fun import UnifiedPumpFun
from model.providers.solana_provider import SolanaProvider

async def main():
    solana_provider = SolanaProvider.get_instance()
    trader = UnifiedPumpFun(solana_provider)
    
    # Auto-detects bonding curve vs DEX
    await trader.buy(mint_str, sol_amount, slippage)
    await trader.sell(mint_str, percentage, slippage)
    
    await trader.close()

asyncio.run(main())
```
