from construct import Struct as cStruct, Byte, Int16ul, Int64ul, Bytes
from solana.rpc.commitment import Processed
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey # type: ignore
from decimal import Decimal
from solana.rpc.types import MemcmpOpts # type: ignore
from config import config
from spl.token.instructions import (
    get_associated_token_address,
)
import logging

# Configure logging
logger = logging.getLogger(__name__)

PUMPSWAP_PROGRAM_ID = config.PUMP_SWAP_PROGRAM
EVENT_AUTHORITY     = Pubkey.from_string(config.get('constants.event_authority_pump_swap'))

PROTOCOL_FEE_RECIP  = Pubkey.from_string(config.get('constants.protocol_fee_recipient'))
PROTOCOL_FEE_RECIP_ATA = Pubkey.from_string(config.get('constants.protocol_fee_recipient_ata'))
CREATE_POOL_DISCRIM = bytes.fromhex(config.get('constants.create_pool_discrim'))
BUY_INSTR_DISCRIM = bytes.fromhex(config.get('constants.buy_instr_discrim'))
SELL_INSTR_DISCRIM = bytes.fromhex(config.get('constants.sell_instr_discrim'))
WITHDRAW_INSTR_DISCRIM = bytes.fromhex(config.get('withdraw_instr_discrim'))
DEPOSIT_INSTR_DISCRIM = bytes.fromhex(config.get('deposit_instr_discrim'))
LAMPORTS_PER_SOL = config.get('lamports_per_sol')

# Additional constants needed by PumpSwap
TOKEN_PROGRAM_PUB = config.TOKEN_PROGRAM
SYSTEM_PROGRAM_ID = config.SYSTEM_PROGRAM
ASSOCIATED_TOKEN = config.ASSOC_TOKEN_ACC_PROG
GLOBAL_CONFIG_PUB = Pubkey.from_string(config.get('constants.global_config_pump_swap'))
UNIT_COMPUTE_BUDGET = config.get('solana.unit_compute_budget')
NEW_POOL_TYPE = "NEW"
OLD_POOL_TYPE = "OLD"

def get_price(base_balance_tokens: float, quote_balance_sol: float) -> float:
    if base_balance_tokens <= 0:
        return float("inf")
    return quote_balance_sol / base_balance_tokens

CREATOR_VAULT_SEED  = b"creator_vault"

def derive_creator_vault(creator: Pubkey, quote_mint: Pubkey) -> tuple[Pubkey, Pubkey]:
    vault_auth, bump = Pubkey.find_program_address(
        [CREATOR_VAULT_SEED, bytes(creator)],
        PUMPSWAP_PROGRAM_ID
    )
    vault_ata = get_associated_token_address(vault_auth, quote_mint)
    return vault_ata, vault_auth

def convert_sol_to_base_tokens(
    sol_amount: float,
    base_balance_tokens: float,
    quote_balance_sol: float,
    decimals_base: int,
    slippage_pct: float = 0.01
):
    price = get_price(base_balance_tokens, quote_balance_sol)
    raw_tokens = sol_amount / price 
    base_amount_out = int(raw_tokens * (10**decimals_base))

    max_sol = sol_amount * (1 + slippage_pct)
    max_quote_in_lamports = int(max_sol * LAMPORTS_PER_SOL)
    return (base_amount_out, max_quote_in_lamports)

def compute_unit_price_from_total_fee(
    total_lams: int,
    compute_units: int = 120_000
) -> int:
    lamports_per_cu = total_lams / float(compute_units)
    micro_lamports_per_cu = lamports_per_cu * 1_000_000
    return int(micro_lamports_per_cu)

PumpSwapPoolStateNew = cStruct(
    "pool_bump" / Byte,
    "index" / Int16ul,
    "creator" / Bytes(32),
    "base_mint" / Bytes(32),
    "quote_mint" / Bytes(32),
    "lp_mint" / Bytes(32),
    "pool_base_token_account" / Bytes(32),
    "pool_quote_token_account" / Bytes(32),
    "lp_supply" / Int64ul,
    "coin_creator" / Bytes(32),
)
PumpSwapPoolStateOld = cStruct(
    "pool_bump" / Byte,
    "index" / Int16ul,
    "creator" / Bytes(32),
    "base_mint" / Bytes(32),
    "quote_mint" / Bytes(32),
    "lp_mint" / Bytes(32),
    "pool_base_token_account" / Bytes(32),
    "pool_quote_token_account" / Bytes(32),
    "lp_supply" / Int64ul,
)

def convert_pool_keys(container, pool_type):
    return {
        "pool_bump": container.pool_bump,
        "index": container.index,
        "creator": str(Pubkey.from_bytes(container.creator)),
        "base_mint": str(Pubkey.from_bytes(container.base_mint)),
        "quote_mint": str(Pubkey.from_bytes(container.quote_mint)),
        "lp_mint": str(Pubkey.from_bytes(container.lp_mint)),
        "pool_base_token_account": str(Pubkey.from_bytes(container.pool_base_token_account)),
        "pool_quote_token_account": str(Pubkey.from_bytes(container.pool_quote_token_account)),
        "lp_supply": container.lp_supply,
        "coin_creator": str(Pubkey.from_bytes(container.coin_creator)),
    } if pool_type == NEW_POOL_TYPE else {
        "pool_bump": container.pool_bump,
        "index": container.index,
        "creator": str(Pubkey.from_bytes(container.creator)),
        "base_mint": str(Pubkey.from_bytes(container.base_mint)),
        "quote_mint": str(Pubkey.from_bytes(container.quote_mint)),
        "lp_mint": str(Pubkey.from_bytes(container.lp_mint)),
        "pool_base_token_account": str(Pubkey.from_bytes(container.pool_base_token_account)),
        "pool_quote_token_account": str(Pubkey.from_bytes(container.pool_quote_token_account)),
        "lp_supply": container.lp_supply
    }

async def fetch_pool_state(pool: str, async_client: AsyncClient):
    """
        Returns:
            dict: Pool data:
                pool_bump: int
                index: int
                creator: str
                base_mint: str
                quote_mint: str
                lp_mint: str
                pool_base_token_account: str
                pool_quote_token_account: str
                lp_supply: int
                coin_creator: str [Optional]
    """
    pool = Pubkey.from_string(pool)

    resp = await async_client.get_account_info_json_parsed(pool, commitment=Processed)
    if not resp or not resp.value or not resp.value.data:
        raise Exception("Invalid account response")

    raw_data = resp.value.data
    pool_type = NEW_POOL_TYPE
    try:
        parsed = PumpSwapPoolStateNew.parse(raw_data[8:])
    except Exception as e:
        try:
            parsed = PumpSwapPoolStateOld.parse(raw_data[8:])
            pool_type = OLD_POOL_TYPE
        except Exception as e:
            return (None, None)
        
    parsed = convert_pool_keys(parsed, pool_type=pool_type)

    return (parsed, pool_type)


async def async_get_pool_reserves(pool_keys, async_client):
    try:
        vault_quote = Pubkey.from_string(pool_keys["pool_quote_token_account"])
        vault_base = Pubkey.from_string(pool_keys["pool_base_token_account"])

        accounts_resp = await async_client.get_multiple_accounts_json_parsed(
            [vault_quote, vault_base], 
            commitment=Processed
        )
        accounts_data = accounts_resp.value

        account_quote = accounts_data[0]
        account_base = accounts_data[1]
        
        quote_balance = account_quote.data.parsed['info']['tokenAmount']['uiAmount']
        base_balance = account_base.data.parsed['info']['tokenAmount']['uiAmount']
        
        if quote_balance is None or base_balance is None:
            print("Error: One of the account balances is None.")
            return None, None
        
        return base_balance, quote_balance

    except Exception as exc:
        print(f"Error fetching pool reserves: {exc}")
        return None, None
    
async def fetch_pool_base_price(pool_keys, async_client):
    balance_base, balance_quote = await async_get_pool_reserves(pool_keys, async_client)
    if balance_base is None or balance_quote is None:
        print("Error: One of the account balances is None.")
        return None
    price = Decimal(balance_quote) / Decimal(balance_base)
    return (price, balance_base, balance_quote)

def derive_pool_address_pump_swap(creator: Pubkey, base_mint: Pubkey,
                        quote_mint: Pubkey, index: int = 0) -> Pubkey:
    seed = [
        b"pool",
        index.to_bytes(2, "little"),
        bytes(creator),
        bytes(base_mint),
        bytes(quote_mint),
    ]
    return Pubkey.find_program_address(seed, PUMPSWAP_PROGRAM_ID)[0]


# Pool Discovery Functions

def calculate_pool_score(pool_data: dict, pool_type: str) -> float:
    """
    Calculate a score for pool selection based on liquidity and other factors.
    Higher score = better pool.
    """
    try:
        # Primary factor: Total liquidity (SOL + token value)
        sol_liquidity = pool_data.get('quote_balance_sol', 0)
        token_liquidity = pool_data.get('base_balance_tokens', 0)
        
        # Calculate total liquidity in SOL terms (rough estimate)
        if token_liquidity > 0 and sol_liquidity > 0:
            price_per_token = sol_liquidity / token_liquidity
            total_liquidity_sol = sol_liquidity * 2  # Assume balanced pool
        else:
            total_liquidity_sol = sol_liquidity
        
        # Bonus for NEW pools (more features)
        pool_type_bonus = 1.1 if pool_type == NEW_POOL_TYPE else 1.0
        
        score = total_liquidity_sol * pool_type_bonus
        return score
        
    except Exception:
        return 0.0


async def find_pools_by_mint(mint_str: str, async_client: AsyncClient) -> tuple[bool, list]:
    """
    Find all pools for a mint address using on-chain search.
    Returns: (found, pool_candidates_list)
    """
    try:
        
        target_mint = Pubkey.from_string(mint_str)
        
        # Search strategy: Use memcmp to filter pools by base_mint field
        # The base_mint is located at offset 8 + 1 + 2 + 32 = 43 bytes from start
        mint_filter = MemcmpOpts(
            offset=43,  # Position of base_mint in pool state
            bytes=str(target_mint)
        )
        
        # Get program accounts with mint filter
        response = await async_client.get_program_accounts(
            PUMPSWAP_PROGRAM_ID,
            encoding="base64",
            filters=[mint_filter],
            commitment=Processed
        )
        
        if not response.value:
            return False, []
        
        # Process all pools and collect valid ones
        pool_candidates = []
        
        for account_info in response.value:
            try:
                pool_address = str(account_info.pubkey)
                account_data = account_info.account.data
                
                # Try to parse as NEW pool first, then OLD
                pool_type = NEW_POOL_TYPE
                parsed_pool = None
                
                try:
                    parsed_pool = PumpSwapPoolStateNew.parse(account_data[8:])
                    pool_type = NEW_POOL_TYPE
                except Exception:
                    try:
                        parsed_pool = PumpSwapPoolStateOld.parse(account_data[8:])
                        pool_type = OLD_POOL_TYPE
                    except Exception:
                        continue
                
                if not parsed_pool:
                    continue
                
                # Convert to dict format
                pool_keys = convert_pool_keys(parsed_pool, pool_type)
                
                # Verify this is indeed our mint
                if pool_keys["base_mint"] != mint_str:
                    continue
                
                # Store pool candidate
                pool_candidates.append({
                    "pool_address": pool_address,
                    "pool_keys": pool_keys,
                    "pool_type": pool_type,
                    "account_data": account_data
                })
                
            except Exception:
                continue
        
        return len(pool_candidates) > 0, pool_candidates
        
    except ImportError as e:
        logger.error(f"Missing required imports: {e}")
        return False, []
        
    except Exception as e:
        logger.error(f"Error in pool discovery: {type(e).__name__}: {str(e)}")
        return False, []


async def find_best_pool_by_mint(mint_str: str, async_client: AsyncClient, pump_swap_client) -> tuple[bool, dict, str]:
    """
    Find the best pool for a mint address, considering multiple pools if they exist.
    Returns: (found, best_pool_data, pool_type)
    """
    try:
        # Find all pools for this mint
        found, pool_candidates = await find_pools_by_mint(mint_str, async_client)
        
        if not found or not pool_candidates:
            return False, {}, ""
        
        # Process candidates and fetch additional data
        scored_pools = []
        
        for candidate in pool_candidates:
            try:
                pool_address = candidate["pool_address"]
                pool_keys = candidate["pool_keys"]
                pool_type = candidate["pool_type"]
                
                # Fetch additional pool data (pricing, reserves)
                base_price, base_balance_tokens, quote_balance_sol = await pump_swap_client.fetch_pool_base_price(pool_address)
                
                # Get mint info for decimals
                mint_info = await async_client.get_account_info_json_parsed(
                    Pubkey.from_string(mint_str),
                    commitment=Processed
                )
                
                if not mint_info:
                    continue
                
                dec_base = mint_info.value.data.parsed['info']['decimals']
                
                # Prepare complete pool data
                pool_data = {
                    "pool_pubkey": Pubkey.from_string(pool_address),
                    "token_base": Pubkey.from_string(pool_keys["base_mint"]),
                    "token_quote": Pubkey.from_string(pool_keys["quote_mint"]),
                    "pool_base_token_account": pool_keys["pool_base_token_account"],
                    "pool_quote_token_account": pool_keys["pool_quote_token_account"],
                    "base_balance_tokens": base_balance_tokens,
                    "quote_balance_sol": quote_balance_sol,
                    "decimals_base": dec_base,
                }
                
                if pool_type == NEW_POOL_TYPE:
                    pool_data["coin_creator"] = Pubkey.from_string(pool_keys["coin_creator"])
                
                # Calculate pool score for ranking
                score = calculate_pool_score(pool_data, pool_type)
                
                scored_pools.append({
                    "pool_data": pool_data,
                    "pool_type": pool_type,
                    "pool_address": pool_address,
                    "score": score,
                    "sol_liquidity": quote_balance_sol
                })
                
            except Exception:
                continue
        
        if not scored_pools:
            return False, {}, ""
        
        # Sort by score and select the best pool
        scored_pools.sort(key=lambda x: x["score"], reverse=True)
        best_pool = scored_pools[0]
        
        return True, best_pool["pool_data"], best_pool["pool_type"]
        
    except Exception as e:
        logger.error(f"Error in best pool discovery: {e}")
        return False, {}, ""
