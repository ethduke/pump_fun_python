# PumpFun Python

PumpFun Python automatically detects if a token uses a bonding curve or PumpSwap DEX, and executes buy/sell operations using the correct method.

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
