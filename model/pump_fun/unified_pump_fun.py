import logging
from typing import Optional, Tuple
from abc import ABC, abstractmethod

from solana.rpc.async_api import AsyncClient

from model.pump_fun.bonding_curve.pump_fun import PumpFun
from model.pump_fun.pump_swap.pump_swap import PumpSwap
from model.providers.solana_provider import SolanaProvider
from utils.coin_data import get_coin_data
from utils.pool_utils import find_best_pool_by_mint
from config import config

logger = logging.getLogger(__name__)


class TradingStrategy(ABC):
    """Abstract base class for trading strategies"""
    
    @abstractmethod
    async def buy(self, mint_str: str, sol_amount: float, slippage: int = 15, **kwargs) -> bool:
        """Execute buy operation"""
        pass
    
    @abstractmethod
    async def sell(self, mint_str: str, percentage: int = 100, slippage: int = 15, **kwargs) -> bool:
        """Execute sell operation"""
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """Get the name of this strategy"""
        pass


class BondingCurveStrategy(TradingStrategy):
    """Strategy for trading on bonding curve (pump.fun)"""
    
    def __init__(self, pump_fun: PumpFun):
        self.pump_fun = pump_fun
    
    async def buy(self, mint_str: str, sol_amount: float, slippage: int = 15, **kwargs) -> bool:
        """Execute buy on bonding curve"""
        return self.pump_fun.buy_bonding_curve(mint_str, sol_amount, slippage)
    
    async def sell(self, mint_str: str, percentage: int = 100, slippage: int = 15, **kwargs) -> bool:
        """Execute sell on bonding curve"""
        return self.pump_fun.sell_bonding_curve(mint_str, percentage, slippage)
    
    def get_strategy_name(self) -> str:
        return "BondingCurve"


class PumpSwapStrategy(TradingStrategy):
    """Strategy for trading on PumpSwap DEX"""
    
    def __init__(self, pump_swap: PumpSwap, async_client: AsyncClient):
        self.pump_swap = pump_swap
        self.async_client = async_client
        self._pool_cache = {}  # Cache pools to avoid repeated lookups
    
    async def _get_pool_data(self, mint_str: str) -> Tuple[bool, dict, str]:
        """Get pool data for mint, with caching"""
        if mint_str in self._pool_cache:
            return self._pool_cache[mint_str]
        
        found, pool_data, pool_type = await find_best_pool_by_mint(
            mint_str, self.async_client, self.pump_swap
        )
        
        if found:
            self._pool_cache[mint_str] = (found, pool_data, pool_type)
        
        return found, pool_data, pool_type
    
    async def buy(self, mint_str: str, sol_amount: float, slippage: int = 15, 
                  fee_sol: float = 0.0005, **kwargs) -> bool:
        """Execute buy on PumpSwap"""
        found, pool_data, pool_type = await self._get_pool_data(mint_str)
        if not found:
            logger.error(f"No pool found for mint {mint_str}")
            return False
        
        result = await self.pump_swap.buy(
            pool_data=pool_data,
            sol_amount=sol_amount,
            pool_type=pool_type,
            slippage_pct=slippage,
            fee_sol=fee_sol,
            debug_prints=False
        )
        return result[0]  # Return confirmed status
    
    async def sell(self, mint_str: str, percentage: int = 100, slippage: int = 15,
                   fee_sol: float = 0.0005, **kwargs) -> bool:
        """Execute sell on PumpSwap"""
        found, pool_data, pool_type = await self._get_pool_data(mint_str)
        if not found:
            logger.error(f"No pool found for mint {mint_str}")
            return False
        
        result = await self.pump_swap.sell(
            pool_data=pool_data,
            sell_pct=percentage,
            pool_type=pool_type,
            slippage_pct=slippage,
            fee_sol=fee_sol,
            debug_prints=False
        )
        return result[0]  # Return confirmed status
    
    def get_strategy_name(self) -> str:
        return "PumpSwap"


class UnifiedPumpFun:
    """
    Unified interface for trading on both bonding curve and PumpSwap.
    Automatically detects token state and uses appropriate strategy.
    """
    
    def __init__(self, solana_provider: Optional[SolanaProvider] = None):
        self._provider = solana_provider or SolanaProvider.get_instance()
        # Create AsyncClient using HTTP RPC URL, not WebSocket URL
        rpc_url = config.get('env.helius.rpc_url')
        self._async_client = AsyncClient(rpc_url)
        
        # Initialize both strategies
        self._pump_fun = PumpFun(self._provider)  # Uses sync client
        self._pump_swap = PumpSwap(self._async_client, self._provider.payer)  # Uses async client
        
        self._bonding_strategy = BondingCurveStrategy(self._pump_fun)
        self._pumpswap_strategy = PumpSwapStrategy(self._pump_swap, self._async_client)
        
        # Cache for token states to avoid repeated API calls
        self._token_state_cache = {}
    
    async def _detect_trading_strategy(self, mint_str: str) -> Optional[TradingStrategy]:
        """
        Detect which trading strategy to use based on token state.
        Returns appropriate strategy or None if token is invalid.
        """
        if mint_str in self._token_state_cache:
            is_valid, is_on_bonding_curve = self._token_state_cache[mint_str]
        else:
            # Check token state
            try:
                coin_data = get_coin_data(mint_str)
                if not coin_data:
                    logger.error(f"Invalid mint or network issue for {mint_str}")
                    return None
                
                is_valid = True
                is_on_bonding_curve = not coin_data.complete
                
                # Cache the result
                self._token_state_cache[mint_str] = (is_valid, is_on_bonding_curve)
                
            except Exception as e:
                logger.error(f"Error detecting strategy for {mint_str}: {e}")
                return None
        
        if not is_valid:
            return None
        
        if is_on_bonding_curve:
            logger.info(f"Using bonding curve strategy for {mint_str}")
            return self._bonding_strategy
        else:
            logger.info(f"Using PumpSwap strategy for {mint_str}")
            return self._pumpswap_strategy
    
    async def buy(self, mint_str: str, sol_amount: float, slippage: int = 15, **kwargs) -> bool:
        """
        Buy tokens using the appropriate strategy based on token state.
        
        Args:
            mint_str: Token mint address
            sol_amount: Amount of SOL to spend
            slippage: Slippage tolerance percentage
            **kwargs: Additional arguments (fee_sol for PumpSwap, etc.)
        
        Returns:
            bool: True if successful, False otherwise
        """
        strategy = await self._detect_trading_strategy(mint_str)
        if not strategy:
            logger.error(f"Could not determine trading strategy for {mint_str}")
            return False
        
        logger.info(f"Executing buy with {strategy.get_strategy_name()} strategy")
        
        try:
            return await strategy.buy(mint_str, sol_amount, slippage, **kwargs)
        except Exception as e:
            logger.error(f"Buy failed with {strategy.get_strategy_name()}: {e}")
            return False
    
    async def sell(self, mint_str: str, percentage: int = 100, slippage: int = 15, **kwargs) -> bool:
        """
        Sell tokens using the appropriate strategy based on token state.
        
        Args:
            mint_str: Token mint address
            percentage: Percentage of holdings to sell (1-100)
            slippage: Slippage tolerance percentage
            **kwargs: Additional arguments (fee_sol for PumpSwap, etc.)
        
        Returns:
            bool: True if successful, False otherwise
        """
        strategy = await self._detect_trading_strategy(mint_str)
        if not strategy:
            logger.error(f"Could not determine trading strategy for {mint_str}")
            return False
        
        logger.info(f"Executing sell with {strategy.get_strategy_name()} strategy")
        
        try:
            return await strategy.sell(mint_str, percentage, slippage, **kwargs)
        except Exception as e:
            logger.error(f"Sell failed with {strategy.get_strategy_name()}: {e}")
            return False
    
    async def get_token_info(self, mint_str: str) -> dict:
        """Get comprehensive token information"""
        try:
            coin_data = get_coin_data(mint_str)
            if not coin_data:
                return {"valid": False, "error": "Invalid mint or network issue"}
            
            info = {
                "valid": True,
                "mint": mint_str,
                "name": getattr(coin_data, 'name', 'Unknown'),
                "symbol": getattr(coin_data, 'symbol', 'Unknown'),
                "is_on_bonding_curve": not coin_data.complete,
                "creator": str(coin_data.creator),
                "market_cap": getattr(coin_data, 'market_cap', 0),
            }
            
            if coin_data.complete:
                # Try to get pool info
                found, pool_data, pool_type = await find_best_pool_by_mint(
                    mint_str, self._async_client, self._pump_swap
                )
                if found:
                    info["pool_available"] = True
                    info["pool_address"] = str(pool_data["pool_pubkey"])
                    info["pool_type"] = pool_type
                    info["liquidity_sol"] = pool_data["quote_balance_sol"]
                else:
                    info["pool_available"] = False
            else:
                info["virtual_sol_reserves"] = coin_data.virtual_sol_reserves
                info["virtual_token_reserves"] = coin_data.virtual_token_reserves
            
            return info
            
        except Exception as e:
            return {"valid": False, "error": str(e)}
    

    
    async def close(self):
        """Clean up resources"""
        await self._pump_swap.close()
        await self._async_client.close()
