import struct
import logging
from typing import Optional, List
from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts
from spl.token.instructions import (
    CloseAccountParams,
    close_account,
    create_associated_token_account,
    get_associated_token_address,
)
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price  # type: ignore
from solders.instruction import Instruction, AccountMeta  # type: ignore
from solders.message import MessageV0  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore
from solders.pubkey import Pubkey # type: ignore
from config import config
from utils.common_utils import confirm_txn, get_token_balance
from utils.coin_data import get_coin_data, tokens_for_sol
from model.providers.solana_provider import SolanaProvider

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Solana provider
solana_provider = SolanaProvider.get_instance()
client = solana_provider.rpc
payer_keypair = solana_provider.payer

class PumpFun:

    def __init__(self, solana_provider: Optional[SolanaProvider] = None):
        self.payer_keypair = payer_keypair
            
        # Initialize providers
        self._provider = solana_provider or SolanaProvider.get_instance()
        self._client = self._provider.rpc

    def execute_versioned_transaction(self, versioned_transaction: VersionedTransaction) -> bool:
        """
        Execute a versioned transaction.
        
        Args:
            versioned_transaction (VersionedTransaction): The versioned transaction to execute
            
        Returns:
            bool: True if transaction successful, False otherwise
        """
        try:
            if not versioned_transaction:
                logger.error("No transaction provided for execution")
                return False
                
            logger.info("Sending versioned transaction")
            txn_sig = self._client.send_transaction(
                txn=versioned_transaction,
                opts=TxOpts(skip_preflight=True),
            ).value
            logger.info(f"Transaction Signature: {txn_sig}")

            logger.info("Confirming transaction")
            confirmed = confirm_txn(txn_sig)
            logger.info(f"Transaction confirmed: {confirmed}")
            return confirmed
        except Exception as e:
            logger.error(f"Error executing versioned transaction: {e}")
            return False

    def create_swap_instruction(self, operation_code: str, mint: Pubkey, bonding_curve: Pubkey, 
                               associated_bonding_curve: Pubkey, associated_user: Pubkey, 
                               user: Pubkey, creator_vault: Pubkey, amount: int, 
                               sol_amount: int, is_buy: bool = True) -> Instruction:
        """Create swap instruction for buy or sell operations"""
        
        if is_buy:
            # Buy order: GLOBAL, FEE_RECIPIENT, MINT, BONDING_CURVE, ASSOCIATED_BONDING_CURVE, 
            # ASSOCIATED_USER, USER, SYSTEM_PROGRAM, TOKEN_PROGRAM, CREATOR_VAULT, EVENT_AUTHORITY, PUMP_FUN_PROGRAM
            keys = [
                AccountMeta(pubkey=config.GLOBAL, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.FEE_RECIPIENT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
                AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
                AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
                AccountMeta(pubkey=associated_user, is_signer=False, is_writable=True),
                AccountMeta(pubkey=user, is_signer=True, is_writable=True),
                AccountMeta(pubkey=config.SYSTEM_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=creator_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=config.EVENT_AUTHORITY, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
            ]
        else:
            # Sell order: GLOBAL, FEE_RECIPIENT, MINT, BONDING_CURVE, ASSOCIATED_BONDING_CURVE, 
            # ASSOCIATED_USER, USER, SYSTEM_PROGRAM, CREATOR_VAULT, TOKEN_PROGRAM, EVENT_AUTHORITY, PUMP_FUN_PROGRAM
            keys = [
                AccountMeta(pubkey=config.GLOBAL, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.FEE_RECIPIENT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
                AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
                AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
                AccountMeta(pubkey=associated_user, is_signer=False, is_writable=True),
                AccountMeta(pubkey=user, is_signer=True, is_writable=True),
                AccountMeta(pubkey=config.SYSTEM_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=creator_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=config.TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.EVENT_AUTHORITY, is_signer=False, is_writable=False),
                AccountMeta(pubkey=config.PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
            ]

        data = bytearray()
        data.extend(bytes.fromhex(operation_code))
        data.extend(struct.pack('<Q', amount))
        data.extend(struct.pack('<Q', sol_amount))
        
        return Instruction(config.PUMP_FUN_PROGRAM, bytes(data), keys)

    def create_versioned_swap_transaction(self, instructions: List[Instruction]) -> VersionedTransaction:
        """Create a versioned transaction with the provided instructions"""
        
        print("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        return VersionedTransaction(compiled_message, [payer_keypair])

    def buy_bonding_curve(self, mint_str: str, sol_in: float = 0.01, slippage: int = 5) -> bool:
        try:
            print(f"Starting buy transaction for mint: {mint_str}")

            coin_data = get_coin_data(mint_str)
            
            if not coin_data:
                print("Failed to retrieve coin data.")
                return False

            if coin_data.complete:
                print("Warning: This token has bonded and is only tradable on PumpSwap.")
                return False

            MINT = coin_data.mint
            BONDING_CURVE = coin_data.bonding_curve
            ASSOCIATED_BONDING_CURVE = coin_data.associated_bonding_curve
            USER = payer_keypair.pubkey()
            creator = coin_data.creator
            CREATOR_VAULT,_ = Pubkey.find_program_address([b'creator-vault', bytes(creator)], config.PUMP_FUN_PROGRAM)

            print("Fetching or creating associated token account...")
            
            token_account_check = client.get_token_accounts_by_owner(payer_keypair.pubkey(), TokenAccountOpts(MINT), Processed)
            
            additional_instructions = []
            if token_account_check.value:
                ASSOCIATED_USER = token_account_check.value[0].pubkey
                print("Existing token account found.")
            else:
                ASSOCIATED_USER = get_associated_token_address(USER, MINT)
                token_account_instruction = create_associated_token_account(USER, USER, MINT)
                additional_instructions.append(token_account_instruction)
                print(f"Creating token account : {ASSOCIATED_USER}")

            print("Calculating transaction amounts...")
            sol_dec = 1e9
            token_dec = 1e6
            
            # Try a very small fixed token amount to test (equivalent to ~0.001 SOL worth)
            amount = int(1000 * token_dec)  # 1000 tokens
            
            slippage_adjustment = 1 + (slippage / 100)
            max_sol_cost = int((sol_in * slippage_adjustment) * sol_dec)
            print(f"Amount: {amount} | Max Sol Cost: {max_sol_cost}")

            print("Creating swap instructions...")
            swap_instruction = self.create_swap_instruction(
                operation_code="66063d1201daebea",
                mint=MINT,
                bonding_curve=BONDING_CURVE,
                associated_bonding_curve=ASSOCIATED_BONDING_CURVE,
                associated_user=ASSOCIATED_USER,
                user=USER,
                creator_vault=CREATOR_VAULT,
                amount=amount,
                sol_amount=max_sol_cost,
                is_buy=True
            )

            # Build instructions array with proper ordering
            instructions = [
                set_compute_unit_limit(config.UNIT_BUDGET),
                set_compute_unit_price(config.UNIT_PRICE),
            ]
            
            # Add token account creation BEFORE swap if needed
            if additional_instructions:
                print(f"Adding {len(additional_instructions)} pre-swap instructions (token account creation)")
                instructions.extend(additional_instructions)
            
            # Add swap instruction AFTER account creation
            print("Adding swap instruction")
            instructions.append(swap_instruction)
            
            print(f"Total instructions: {len(instructions)}")
            versioned_txn = self.create_versioned_swap_transaction(instructions)

            print("Executing transaction...")
            confirmed = self.execute_versioned_transaction(versioned_txn)
            
            return confirmed
        except Exception as e:
            print(f"Error occurred during transaction: {e}")
            return False

    def sell_bonding_curve(self, mint_str: str, percentage: int = 100, slippage: int = 5) -> bool:
        try:
            print(f"Starting sell transaction for mint: {mint_str}")

            if not (1 <= percentage <= 100):
                print("Percentage must be between 1 and 100.")
                return False

            coin_data = get_coin_data(mint_str)
            
            if not coin_data:
                print("Failed to retrieve coin data.")
                return False

            if coin_data.complete:
                print("Warning: This token has bonded and is only tradable on PumpSwap.")
                return False

            MINT = coin_data.mint
            BONDING_CURVE = coin_data.bonding_curve
            ASSOCIATED_BONDING_CURVE = coin_data.associated_bonding_curve
            USER = payer_keypair.pubkey()
            ASSOCIATED_USER = get_associated_token_address(USER, MINT)
            creator = coin_data.creator
            CREATOR_VAULT, _ = Pubkey.find_program_address([b'creator-vault', bytes(creator)], config.PUMP_FUN_PROGRAM)

            print("Retrieving token balance...")
            token_balance = get_token_balance(payer_keypair.pubkey(), MINT)
            if token_balance == 0 or token_balance is None:
                print("Token balance is zero. Nothing to sell.")
                return False
            print(f"Token Balance: {token_balance}")
            
            print("Calculating transaction amounts...")
            sol_dec = 1e9
            token_dec = 1e6
            token_balance = token_balance * (percentage / 100)
            amount = int(token_balance * token_dec)
            
            virtual_sol_reserves = coin_data.virtual_sol_reserves / sol_dec
            virtual_token_reserves = coin_data.virtual_token_reserves / token_dec
            sol_out = tokens_for_sol(token_balance, virtual_sol_reserves, virtual_token_reserves)
            
            slippage_adjustment = 1 - (slippage / 100)
            min_sol_output = int((sol_out * slippage_adjustment) * sol_dec)
            print(f"Amount: {amount} | Minimum Sol Out: {min_sol_output}")
            
            print("Creating swap instructions...")
            swap_instruction = self.create_swap_instruction(
                operation_code="33e685a4017f83ad",
                mint=MINT,
                bonding_curve=BONDING_CURVE,
                associated_bonding_curve=ASSOCIATED_BONDING_CURVE,
                associated_user=ASSOCIATED_USER,
                user=USER,
                creator_vault=CREATOR_VAULT,
                amount=amount,
                sol_amount=min_sol_output,
                is_buy=False
            )

            # Prepare close instruction for 100% sells (executed AFTER swap)
            additional_instructions = []
            if percentage == 100:
                print("Preparing to close token account after swap...")
                close_account_instruction = close_account(CloseAccountParams(
                    program_id=config.TOKEN_PROGRAM,
                    account=ASSOCIATED_USER,
                    dest=USER,  # Fixed: use 'dest' not 'destination'
                    owner=USER
                ))
                additional_instructions.append(close_account_instruction)

            # Build instructions array with proper ordering
            instructions = [
                set_compute_unit_limit(config.UNIT_BUDGET),
                set_compute_unit_price(config.UNIT_PRICE),
                swap_instruction,  # Swap instruction first
            ]
            
            # Add close account AFTER swap if needed
            if additional_instructions:
                print(f"Adding {len(additional_instructions)} post-swap instructions (close account)")
                instructions.extend(additional_instructions)
            
            print(f"Total instructions: {len(instructions)}")
            versioned_txn = self.create_versioned_swap_transaction(instructions)

            print("Executing transaction...")
            confirmed = self.execute_versioned_transaction(versioned_txn)
            
            return confirmed

        except Exception as e:
            print(f"Error occurred during transaction: {e}")
            return False
