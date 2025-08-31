#!/usr/bin/env python3
"""
Pump.fun Trading Bot - Unified Trading Interface
"""

import asyncio
import logging
import sys

from config import Config
from src.pump_fun.unified_pump_fun import UnifiedPumpFun # type: ignore
from src.providers.solana_provider import SolanaProvider

logging.getLogger("httpx").setLevel(logging.WARNING)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


BUFFER_FEES_SOL = 0.01
FEE_SOL = 0.0005
DEFAULT_SLIPPAGE = 25
LOSS_PERCENTAGE = 0.1
PERCENTAGE_SELL = 100
MIN_SOL_BALANCE = 0.02

class SimplePumpTester:
    """Simplified pump trading tester using unified interface"""
    
    def __init__(self):
        try:
            self.config = Config()
            self.solana_provider = SolanaProvider.get_instance()
            self.trader = UnifiedPumpFun(self.solana_provider)
            self.payer_pubkey = self.solana_provider.payer.pubkey()
            print(f"Wallet: {self.payer_pubkey}")
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            raise
    
    async def check_wallet_balance(self) -> float:
        """Check SOL balance"""
        try:
            balance_response = self.solana_provider.rpc.get_balance(self.payer_pubkey)
            balance = balance_response.value
            sol_balance = balance / 1e9
            return sol_balance
        except Exception as e:
            logger.error(f"Failed to check wallet balance: {e}")
            return 0.0
    
    async def get_token_info(self, mint_str: str):
        """Get and display token information"""
        print(f"\n📊 Getting token info for: {mint_str}")
        
        info = await self.trader.get_token_info(mint_str)
        
        if not info["valid"]:
            print(f"❌ Error: {info.get('error', 'Unknown error')}")
            return False
        
        print(f"   Name: {info.get('name', 'Unknown')}")
        print(f"   Symbol: {info.get('symbol', 'Unknown')}")
        print(f"   Trading venue: {'Bonding Curve (pump.fun)' if info['is_on_bonding_curve'] else 'PumpSwap DEX'}")
        
        if info['is_on_bonding_curve']:
            print(f"   SOL reserves: {info.get('virtual_sol_reserves', 0) / 1e9:.4f}")
            print(f"   Token reserves: {info.get('virtual_token_reserves', 0) / 1e6:.0f}")
        else:
            if info.get('pool_available'):
                print(f"   Pool address: {info['pool_address']}")
                print(f"   Pool type: {info['pool_type']}")
                print(f"   SOL liquidity: {info.get('liquidity_sol', 0):.4f}")
            else:
                print("   ⚠️ No pool found - token may not be tradeable")
        
        return True
    
    async def test_buy_sell_cycle(self, mint_str: str, test_sol_amount: float = 0.01):
        """Test complete buy/sell cycle with unified interface"""
        print(f"\n🚀 Starting buy/sell test for {mint_str}")
        print(f"Test amount: {test_sol_amount} SOL")
        
        # Check initial balance
        initial_sol_balance = await self.check_wallet_balance()
        print(f"Initial SOL balance: {initial_sol_balance:.4f}")
        
        if initial_sol_balance < test_sol_amount + BUFFER_FEES_SOL:  
            print(f"❌ Insufficient SOL. Need {test_sol_amount + BUFFER_FEES_SOL:.4f}, have {initial_sol_balance:.4f}")
            return False
        
        # Get token info
        if not await self.get_token_info(mint_str):
            return False
        
        # Execute buy
        print(f"\n💰 Executing BUY: {test_sol_amount} SOL")
        buy_success = await self.trader.buy(
            mint_str=mint_str,
            sol_amount=test_sol_amount,
            slippage=DEFAULT_SLIPPAGE,  # Higher slippage for volatile tokens
            fee_sol=FEE_SOL  # This will be ignored for bonding curve
        )
        
        if not buy_success:
            print("❌ Buy failed - trying with higher slippage")
            buy_success = await self.trader.buy(
                mint_str=mint_str,
                sol_amount=test_sol_amount,
                slippage=DEFAULT_SLIPPAGE,
                fee_sol=FEE_SOL
            )
            
            if not buy_success:
                print("❌ Buy failed even with 50% slippage")
                return False
        
        print("✅ Buy successful!")
        print("⏳ Waiting 5 seconds before sell...")
        await asyncio.sleep(5)
        
        # Execute sell
        print(f"\n💸 Executing SELL: 100%")
        sell_success = await self.trader.sell(
            mint_str=mint_str,
            percentage=PERCENTAGE_SELL,
            slippage=DEFAULT_SLIPPAGE,
            fee_sol=FEE_SOL
        )
        
        if not sell_success:
            print("❌ Sell failed")
            return False
        
        print("✅ Sell successful!")
        
        # Check final balance
        await asyncio.sleep(3)
        final_sol_balance = await self.check_wallet_balance()
        net_change = final_sol_balance - initial_sol_balance
        
        print(f"\n📈 Results:")
        print(f"   Initial SOL: {initial_sol_balance:.6f}")
        print(f"   Final SOL: {final_sol_balance:.6f}")
        print(f"   Net change: {net_change:+.6f} SOL")
        
        if net_change > -test_sol_amount * LOSS_PERCENTAGE:  # Lost less than 10% due to fees/slippage
            print("✅ Test completed successfully!")
        else:
            print("⚠️ High losses - check slippage settings")
        
        return True
    
    async def run_multiple_tests(self, mints: list, test_amount: float = 0.002):
        """Run tests on multiple tokens"""
        print(f"\n🔄 Running tests on {len(mints)} tokens")
        
        results = []
        for i, mint in enumerate(mints, 1):
            print(f"\n{'='*60}")
            print(f"Test {i}/{len(mints)}: {mint}")
            print(f"{'='*60}")
            
            try:
                success = await self.test_buy_sell_cycle(mint, test_amount)
                results.append((mint, success))
                
                if i < len(mints):  # Don't wait after last test
                    print("\n⏳ Waiting 10 seconds before next test...")
                    await asyncio.sleep(10)
                    
            except Exception as e:
                logger.error(f"Test failed for {mint}: {e}")
                results.append((mint, False))
        
        # Summary
        print(f"\n{'='*60}")
        print("📊 TEST SUMMARY")
        print(f"{'='*60}")
        successful = sum(1 for _, success in results if success)
        print(f"Total tests: {len(results)}")
        print(f"Successful: {successful}")
        print(f"Failed: {len(results) - successful}")
        
        for mint, success in results:
            status = "✅" if success else "❌"
            print(f"   {status} {mint}")
    
    async def close(self):
        """Clean up resources"""
        await self.trader.close()


async def main():
    print("🚀 Unified Pump.fun Trading Tester")
    print("This tool automatically detects token state and uses appropriate trading method")
    
    tester = None
    try:
        tester = SimplePumpTester()
        
        sol_balance = await tester.check_wallet_balance()
        print(f"SOL balance: {sol_balance:.4f}")
        
        if sol_balance < MIN_SOL_BALANCE:
            print(f"❌ Need at least {MIN_SOL_BALANCE:.4f} SOL for testing")
            return
        
        # Test tokens (mix of bonding curve and graduated tokens)
        test_tokens = [
            "GNWQriPD8w1xuvcP8iemExjZCwXonRw6s9KD34s2pump"
        ]   

        await tester.run_multiple_tests(test_tokens)
        
    finally:
        if tester:
            await tester.close()
            print("\n🔄 Resources cleaned up")

if __name__ == "__main__":
    asyncio.run(main())