#!/usr/bin/env python3
"""
Pump.fun Trading Bot - Main Testing Script
"""

import logging
import time
import sys

from model.pump_fun.bonding_curve.pump_fun import PumpFun
from model.providers.solana_provider import SolanaProvider
from utils.coin_data import get_coin_data
from utils.common_utils import get_token_balance
from solders.pubkey import Pubkey # type: ignore

logging.getLogger("httpx").setLevel(logging.WARNING)

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pump_fun_trading.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class PumpFunTester:
    """A tester for pump.fun trading operations."""
    
    def __init__(self):
        try:
            self.solana_provider = SolanaProvider.get_instance()
            self.pump_fun = PumpFun()
            self.payer_pubkey = self.solana_provider.payer.pubkey()
            print(f"Wallet: {self.payer_pubkey}")
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            raise
    
    def check_wallet_balance(self) -> float:
        try:
            balance = self.solana_provider.rpc.get_balance(self.payer_pubkey).value
            sol_balance = balance / 1e9
            return sol_balance
        except Exception as e:
            logger.error(f"Failed to check wallet balance: {e}")
            return 0.0
    
    def validate_mint_address(self, mint_str: str) -> bool:
        try:
            coin_data = get_coin_data(mint_str)
            if not coin_data:
                logger.error("Invalid mint or network issue")
                return False
            
            if coin_data.complete:
                print("⚠️ Token completed bonding curve")
            
            return True
        except Exception as e:
            logger.error(f"Error validating mint: {e}")
            return False
    
    def get_token_balance_for_mint(self, mint_str: str) -> float:
        try:
            mint_pubkey = Pubkey.from_string(mint_str)
            balance = get_token_balance(self.payer_pubkey, mint_pubkey)
            return balance if balance is not None else 0.0
        except Exception as e:
            logger.error(f"Error getting token balance: {e}")
            return 0.0
    
    def test_buy_operation(self, mint_str: str, sol_amount: float = 0.01, slippage: int = 15) -> bool:
        print(f"Testing BUY: {sol_amount} SOL")
        
        # Safety checks
        sol_balance = self.check_wallet_balance()
        if sol_balance < sol_amount + 0.005:
            print(f"❌ Insufficient SOL. Need {sol_amount + 0.005:.4f}, have {sol_balance:.4f}")
            return False
        
        if not self.validate_mint_address(mint_str):
            print("❌ Invalid mint address")
            return False
        
        initial_token_balance = self.get_token_balance_for_mint(mint_str)
        
        try:
            print("Executing buy...")
            success = self.pump_fun.buy_bonding_curve(mint_str, sol_amount, slippage)
            
            if success:
                time.sleep(3)
                new_token_balance = self.get_token_balance_for_mint(mint_str)
                token_received = new_token_balance - initial_token_balance
                print(f"✅ Buy successful - Received: {token_received:.6f} tokens")
                return True
            else:
                print("❌ Buy failed")
                return False
                
        except Exception as e:
            logger.error(f"Buy error: {e}")
            return False
    
    def test_sell_operation(self, mint_str: str, percentage: int = 100, slippage: int = 15) -> bool:
        print(f"Testing SELL: {percentage}%")
        
        if not self.validate_mint_address(mint_str):
            print("❌ Invalid mint address")
            return False
        
        initial_token_balance = self.get_token_balance_for_mint(mint_str)
        if initial_token_balance <= 0:
            print("❌ No tokens to sell")
            return False
        
        initial_sol_balance = self.check_wallet_balance()
        
        try:
            print("Executing sell...")
            success = self.pump_fun.sell_bonding_curve(mint_str, percentage, slippage)
            
            if success:
                time.sleep(3)
                new_sol_balance = self.check_wallet_balance()
                sol_received = new_sol_balance - initial_sol_balance
                print(f"✅ Sell successful - Received: {sol_received:.6f} SOL")
                return True
            else:
                print("❌ Sell failed")
                return False
                
        except Exception as e:
            logger.error(f"Sell error: {e}")
            return False
    
    def run_comprehensive_test(self, mint_str: str, test_sol_amount: float = 0.01):
        print(f"Starting test with {test_sol_amount} SOL")
        
        initial_sol_balance = self.check_wallet_balance()
        print(f"Initial SOL balance: {initial_sol_balance:.4f}")
        
        # Test buy with higher slippage tolerance
        buy_success = self.test_buy_operation(mint_str, test_sol_amount, slippage=25)  # Increase slippage to 25%
        if not buy_success:
            print("❌ Buy test failed - trying with even higher slippage")
            # Try with much higher slippage for volatile tokens
            buy_success = self.test_buy_operation(mint_str, test_sol_amount, slippage=50)
            if not buy_success:
                print("❌ Buy test failed even with 50% slippage")
                return False
        
        time.sleep(5)
        
        # Test sell
        sell_success = self.test_sell_operation(mint_str, 100)
        if not sell_success:
            print("❌ Sell test failed")
            return False
        
        time.sleep(3)
        final_sol_balance = self.check_wallet_balance()
        net_change = final_sol_balance - initial_sol_balance
        
        print(f"Final SOL balance: {final_sol_balance:.4f}")
        print(f"Net change: {net_change:+.6f} SOL")
        print("✅ Test completed!")
        return True


def main():
    print("🚀 Pump.fun Trading Tester")
    
    try:
        tester = PumpFunTester()
        
        sol_balance = tester.check_wallet_balance()
        print(f"SOL balance: {sol_balance:.4f}")
        
        if sol_balance < 0.02:
            print("❌ Need at least 0.02 SOL for testing")
            return
        
        # Replace with actual token address
        test_mint = "AdrdBM9qRsh8GTp67HGHVWndpLHf1pVKascaKT5Upump"
        
        test_amount = 0.001
        
        print(f"Test token: {test_mint}")
        print(f"Test amount: {test_amount} SOL")


        success = tester.run_comprehensive_test(test_mint, test_amount)
        
        if success:
            print("🎉 All tests passed!")
        else:
            print("❌ Tests failed")
            
    except KeyboardInterrupt:
        print("Test interrupted")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()
