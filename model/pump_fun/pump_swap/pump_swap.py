import struct
from typing import Optional
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.compute_budget import set_compute_unit_price, set_compute_unit_limit # type: ignore
from solders.keypair import Keypair # type: ignore
from solders.pubkey import Pubkey # type: ignore
from solders.transaction import VersionedTransaction # type: ignore
from solders.message import MessageV0 # type: ignore
from solders.signature import Signature # type: ignore
from solana.rpc.commitment import Processed
from solders.instruction import AccountMeta, Instruction # type: ignore
from solders.system_program import TransferParams, transfer
from spl.token.instructions import (
    get_associated_token_address,
    create_associated_token_account,
    sync_native,
    SyncNativeParams,
    close_account,
    CloseAccountParams,
)
from model.providers.solana_token_provider import SolanaTokenProvider
from model.providers.solana_transaction_provider import SolanaTransactionProvider
from utils.pool_utils import (
    fetch_pool_state,
    fetch_pool_base_price,
    derive_creator_vault,
    convert_sol_to_base_tokens,
    compute_unit_price_from_total_fee,
    get_price,
    PUMPSWAP_PROGRAM_ID,
    TOKEN_PROGRAM_PUB,
    SYSTEM_PROGRAM_ID,
    ASSOCIATED_TOKEN,
    EVENT_AUTHORITY,
    GLOBAL_CONFIG_PUB,
    PROTOCOL_FEE_RECIP,
    PROTOCOL_FEE_RECIP_ATA,
    BUY_INSTR_DISCRIM,
    SELL_INSTR_DISCRIM,
    LAMPORTS_PER_SOL,
    UNIT_COMPUTE_BUDGET,
    NEW_POOL_TYPE,
    OLD_POOL_TYPE,
)


class PumpSwap:
    def __init__(self, async_client: AsyncClient, signer: Keypair):
        self.async_client = async_client
        self.signer = signer
        self.token_provider = SolanaTokenProvider()
        self.transaction_provider = SolanaTransactionProvider()
    
    async def close(self):
        await self.async_client.close()

    async def fetch_pool_base_price(self, pool: str):
        """
        Fetch the base price of the pool.
        Args:
            pool (str): Pool address.
        Returns:
            tuple: (base_price, base_balance_tokens, quote_balance_sol)
        """
        pool_keys, _ = await fetch_pool_state(pool, self.async_client)
        base_price, base_balance_tokens, quote_balance_sol = await fetch_pool_base_price(pool_keys, self.async_client)
        return base_price, base_balance_tokens, quote_balance_sol

    async def create_ata_if_needed(self, owner: Pubkey, mint: Pubkey):
        """
        If there's no associated token account for (owner, mint), return an
        instruction to create it. Otherwise return None.
        """
        ata = get_associated_token_address(owner, mint)
        resp = await self.async_client.get_account_info(ata)
        if resp.value is None:
            #  ATA does not exist
            return create_associated_token_account(
                payer=owner,
                owner=owner,
                mint=mint
            )
        return None

    async def _create_ata_if_needed_for_owner(
        self, payer: Pubkey, owner: Pubkey, mint: Pubkey, token_program: Pubkey = TOKEN_PROGRAM_PUB
    ):
        ata = get_associated_token_address(owner, mint, token_program)
        resp = await self.async_client.get_account_info(ata)
        if resp.value is None:
            return create_associated_token_account(
                payer=payer,
                owner=owner,
                mint=mint,
                token_program_id=token_program
            )
        return None

    async def buy(
        self,
        pool_data: dict,
        sol_amount: float,      
        pool_type: str = NEW_POOL_TYPE,
        slippage_pct: float = 10,    
        fee_sol: float = 0.00001,        
        debug_prints: bool = False
    ):
        """
            Args:
                pool_data: dict
                sol_amount: float
                slippage_pct: float
                fee_sol: float
            Returns:
                tuple: (confirmed: bool, tx_sig: str, pool_type: (str)OLD | (str)NEW, (float)mint_amount_we_bought)
        """
        user_pubkey = self.signer.pubkey()
        base_balance_tokens = pool_data['base_balance_tokens']
        quote_balance_sol   = pool_data['quote_balance_sol']
        decimals_base       = pool_data['decimals_base']

        if pool_type == NEW_POOL_TYPE:
            coin_creator  = pool_data["coin_creator"]
            vault_ata, vault_auth = derive_creator_vault(coin_creator, pool_data['token_quote'])

        (base_amount_out, max_quote_amount_in) = convert_sol_to_base_tokens(
            sol_amount, base_balance_tokens, quote_balance_sol,
            decimals_base, slippage_pct
        )

        lamports_fee = int(fee_sol * LAMPORTS_PER_SOL)
        micro_lamports = compute_unit_price_from_total_fee(
            lamports_fee,
            compute_units=UNIT_COMPUTE_BUDGET
        )

        instructions = []

        instructions.append(set_compute_unit_limit(UNIT_COMPUTE_BUDGET))
        instructions.append(set_compute_unit_price(micro_lamports))
        wsol_ata_ix = await self.create_ata_if_needed(user_pubkey, pool_data['token_quote'])
        if wsol_ata_ix:
            instructions.append(wsol_ata_ix)

        wsol_ata = get_associated_token_address(user_pubkey, pool_data['token_quote'])
        system_transfer = transfer(
            TransferParams(
                from_pubkey=user_pubkey,
                to_pubkey=wsol_ata,
                lamports=max_quote_amount_in
            )
        )
        instructions.append(system_transfer)

        instructions.append(
            sync_native(
                SyncNativeParams(
                    program_id=TOKEN_PROGRAM_PUB,
                    account=wsol_ata
                )
            )
        )

        base_ata_ix = await self.create_ata_if_needed(user_pubkey, pool_data['token_base'])
        if base_ata_ix:
            instructions.append(base_ata_ix)

        if pool_type == NEW_POOL_TYPE:
            buy_ix = self._build_new_pumpswap_buy(
                pool_pubkey = pool_data['pool_pubkey'],
                user_pubkey = user_pubkey,
                global_config = GLOBAL_CONFIG_PUB,
                base_mint    = pool_data['token_base'],
                quote_mint   = pool_data['token_quote'],
                user_base_token_ata  = get_associated_token_address(user_pubkey, pool_data['token_base']),
                user_quote_token_ata = get_associated_token_address(user_pubkey, pool_data['token_quote']),
                pool_base_token_account  = Pubkey.from_string(pool_data['pool_base_token_account']),
                pool_quote_token_account = Pubkey.from_string(pool_data['pool_quote_token_account']),
                protocol_fee_recipient   = PROTOCOL_FEE_RECIP,
                protocol_fee_recipient_ata = PROTOCOL_FEE_RECIP_ATA,
                base_amount_out = base_amount_out,
                max_quote_amount_in = max_quote_amount_in,
                vault_auth = vault_auth,
                vault_ata = vault_ata,
            )
        elif pool_type == OLD_POOL_TYPE:
            buy_ix = self._build_old_pumpswap_buy(
                pool_pubkey = pool_data['pool_pubkey'],
                user_pubkey = user_pubkey,
                global_config = GLOBAL_CONFIG_PUB,
                base_mint    = pool_data['token_base'],
                quote_mint   = pool_data['token_quote'],
                user_base_token_ata  = get_associated_token_address(user_pubkey, pool_data['token_base']),
                user_quote_token_ata = get_associated_token_address(user_pubkey, pool_data['token_quote']),
                pool_base_token_account  = Pubkey.from_string(pool_data['pool_base_token_account']),
                pool_quote_token_account = Pubkey.from_string(pool_data['pool_quote_token_account']),
                protocol_fee_recipient   = PROTOCOL_FEE_RECIP,
                protocol_fee_recipient_ata = PROTOCOL_FEE_RECIP_ATA,
                base_amount_out = base_amount_out,
                max_quote_amount_in = max_quote_amount_in
            )
        instructions.append(buy_ix)

        instructions.append(
            close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_PUB,
                    account=wsol_ata,
                    dest=user_pubkey,
                    owner=user_pubkey
                )
            )
        )

        latest_blockhash = await self.async_client.get_latest_blockhash()
        compiled_msg = MessageV0.try_compile(
            payer=user_pubkey,
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=latest_blockhash.value.blockhash,
        )
        transaction = VersionedTransaction(compiled_msg, [self.signer])

        opts = TxOpts(skip_preflight=True, max_retries=0)
        send_resp = await self.async_client.send_transaction(transaction, opts=opts)
        if debug_prints:
            print(f"Transaction sent: https://solscan.io/tx/{send_resp.value}")

        # Confirm
        confirmed = await self.transaction_provider.confirm_transaction(Signature.from_string(str(send_resp.value)))
        if debug_prints:
            print("Success:", confirmed)
        return (confirmed, str(send_resp.value), pool_type, base_amount_out)


    def _build_old_pumpswap_buy(
        self,
        pool_pubkey: Pubkey,
        user_pubkey: Pubkey,
        global_config: Pubkey,
        base_mint: Pubkey,
        quote_mint: Pubkey,
        user_base_token_ata: Pubkey,
        user_quote_token_ata: Pubkey,
        pool_base_token_account: Pubkey,
        pool_quote_token_account: Pubkey,
        protocol_fee_recipient: Pubkey,
        protocol_fee_recipient_ata: Pubkey,
        base_amount_out: int,
        max_quote_amount_in: int
    ):
        """
          #1 Pool
          #2 User
          #3 Global Config
          #4 Base Mint
          #5 Quote Mint
          #6 User Base ATA
          #7 User Quote ATA
          #8 Pool Base ATA
          #9 Pool Quote ATA
          #10 Protocol Fee Recipient
          #11 Protocol Fee Recipient Token Account
          #12 Base Token Program
          #13 Quote Token Program
          #14 System Program
          #15 Associated Token Program
          #16 Event Authority
          #17 PumpSwap Program
        
          {
            base_amount_out:  u64,
            max_quote_amount_in: u64
          }
        plus an 8-byte Anchor discriminator at the front. 
        """
        data = bytearray()
        data.extend(BUY_INSTR_DISCRIM)
        data.extend(struct.pack("<Q", base_amount_out))
        data.extend(struct.pack("<Q", max_quote_amount_in))

        accs = [
            AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True),
            AccountMeta(pubkey=global_config, is_signer=False, is_writable=False),
            AccountMeta(pubkey=base_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=quote_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=user_base_token_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_quote_token_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=pool_base_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=pool_quote_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=protocol_fee_recipient, is_signer=False, is_writable=False),
            AccountMeta(pubkey=protocol_fee_recipient_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=ASSOCIATED_TOKEN, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMPSWAP_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

        return Instruction(
            program_id=PUMPSWAP_PROGRAM_ID,
            data=bytes(data),
            accounts=accs
        )
    
    def _build_new_pumpswap_buy(
        self,
        pool_pubkey: Pubkey,
        user_pubkey: Pubkey,
        global_config: Pubkey,
        base_mint: Pubkey,
        quote_mint: Pubkey,
        user_base_token_ata: Pubkey,
        user_quote_token_ata: Pubkey,
        pool_base_token_account: Pubkey,
        pool_quote_token_account: Pubkey,
        protocol_fee_recipient: Pubkey,
        protocol_fee_recipient_ata: Pubkey,
        base_amount_out: int,
        max_quote_amount_in: int,
        vault_auth: Pubkey,
        vault_ata: Pubkey
    ):
        """
        Updated buy instruction for new pump_amm IDL with volume accumulators.
        
        Accounts (21 total based on new IDL):
          #1  Pool
          #2  User  
          #3  Global Config
          #4  Base Mint
          #5  Quote Mint
          #6  User Base Token Account
          #7  User Quote Token Account
          #8  Pool Base Token Account
          #9  Pool Quote Token Account
          #10 Protocol Fee Recipient
          #11 Protocol Fee Recipient Token Account
          #12 Base Token Program
          #13 Quote Token Program
          #14 System Program
          #15 Associated Token Program
          #16 Event Authority
          #17 Program
          #18 Coin Creator Vault ATA
          #19 Coin Creator Vault Authority
          #20 Global Volume Accumulator
          #21 User Volume Accumulator

        Args:
          base_amount_out: u64
          max_quote_amount_in: u64
        """
        data = bytearray()
        data.extend(BUY_INSTR_DISCRIM)
        data.extend(struct.pack("<Q", base_amount_out))
        data.extend(struct.pack("<Q", max_quote_amount_in))

        # Derive additional required accounts for pump_amm
        global_volume_accumulator = derive_global_volume_accumulator()
        user_volume_accumulator = derive_user_volume_accumulator(user_pubkey)
        event_authority = derive_event_authority()

        accs = [
            AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=False),  # pool
            AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True),   # user
            AccountMeta(pubkey=global_config, is_signer=False, is_writable=False), # global_config
            AccountMeta(pubkey=base_mint, is_signer=False, is_writable=False),   # base_mint
            AccountMeta(pubkey=quote_mint, is_signer=False, is_writable=False),  # quote_mint
            AccountMeta(pubkey=user_base_token_ata, is_signer=False, is_writable=True), # user_base_token_account
            AccountMeta(pubkey=user_quote_token_ata, is_signer=False, is_writable=True), # user_quote_token_account
            AccountMeta(pubkey=pool_base_token_account, is_signer=False, is_writable=True), # pool_base_token_account
            AccountMeta(pubkey=pool_quote_token_account, is_signer=False, is_writable=True), # pool_quote_token_account
            AccountMeta(pubkey=protocol_fee_recipient, is_signer=False, is_writable=False), # protocol_fee_recipient
            AccountMeta(pubkey=protocol_fee_recipient_ata, is_signer=False, is_writable=True), # protocol_fee_recipient_token_account
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False), # base_token_program
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False), # quote_token_program
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False), # system_program
            AccountMeta(pubkey=ASSOCIATED_TOKEN, is_signer=False, is_writable=False), # associated_token_program
            AccountMeta(pubkey=event_authority, is_signer=False, is_writable=False), # event_authority
            AccountMeta(pubkey=PUMPSWAP_PROGRAM_ID, is_signer=False, is_writable=False), # program
            AccountMeta(pubkey=vault_ata, is_signer=False, is_writable=True), # coin_creator_vault_ata
            AccountMeta(pubkey=vault_auth, is_signer=False, is_writable=False), # coin_creator_vault_authority
            AccountMeta(pubkey=global_volume_accumulator, is_signer=False, is_writable=True), # global_volume_accumulator
            AccountMeta(pubkey=user_volume_accumulator, is_signer=False, is_writable=True), # user_volume_accumulator
        ]

        return Instruction(
            program_id=PUMPSWAP_PROGRAM_ID,
            data=bytes(data),
            accounts=accs
        )

    async def sell(
        self,
        pool_data: dict,
        sell_pct: float,
        pool_type: str = NEW_POOL_TYPE,
        slippage_pct: float = 10, 
        fee_sol: float = 0.00001,
        debug_prints: bool = False
    ):
        """
            Args:
                pool_data: dict
                sell_pct: float
                pool_type: str
                slippage_pct: float
                fee_sol: float
            Returns:
                tuple: (confirmed: bool, tx_sig: str, pool_type: (str)OLD | (str)NEW, (float)mint_amount_we_sold)
        """
        user_pubkey = self.signer.pubkey()
        
        user_base_balance_f = await self.token_provider.get_token_balance(str(pool_data['token_base']))
        if user_base_balance_f is None or user_base_balance_f <= 0:
            if debug_prints:
                print("No base token balance, can't sell.")
            return (False, None, pool_type)
        
        to_sell_amount_f = user_base_balance_f * (sell_pct / 100.0)
        if to_sell_amount_f <= 0:
            if debug_prints:
                print("Nothing to sell after applying percentage.")
            return (False, None, pool_type)

        if pool_type == NEW_POOL_TYPE:
            coin_creator  = pool_data["coin_creator"]
            vault_ata, vault_auth = derive_creator_vault(coin_creator, pool_data['token_quote'])

        decimals_base = pool_data['decimals_base']
        base_amount_in = int(to_sell_amount_f * (10 ** decimals_base))
        
        base_balance_tokens = pool_data['base_balance_tokens']
        quote_balance_sol   = pool_data['quote_balance_sol']
        
        price = get_price(base_balance_tokens, quote_balance_sol)
        raw_sol = to_sell_amount_f * price
        
        min_sol_out = raw_sol * (1 - slippage_pct/100.0)
        min_quote_amount_out = int(min_sol_out * LAMPORTS_PER_SOL)
        if min_quote_amount_out <= 0:
            if debug_prints:
                print("min_quote_amount_out <= 0. Slippage too big or no liquidity.")
            return (False, None, pool_type)
        
        lamports_fee = int(fee_sol * LAMPORTS_PER_SOL)
        micro_lamports = compute_unit_price_from_total_fee(
            lamports_fee,
            compute_units=UNIT_COMPUTE_BUDGET
        )
        
        instructions = []
        instructions.append(set_compute_unit_limit(UNIT_COMPUTE_BUDGET))
        instructions.append(set_compute_unit_price(micro_lamports))
        
        wsol_ata_ix = await self.create_ata_if_needed(user_pubkey, pool_data['token_quote'])
        if wsol_ata_ix:
            instructions.append(wsol_ata_ix)
        
        if pool_type == NEW_POOL_TYPE:
            sell_ix = self._build_new_pumpswap_sell(
                user_pubkey = user_pubkey,
                pool_data = pool_data,
                base_amount_in = base_amount_in,
                min_quote_amount_out = min_quote_amount_out,
                protocol_fee_recipient   = PROTOCOL_FEE_RECIP,
                protocol_fee_recipient_ata = PROTOCOL_FEE_RECIP_ATA,
                vault_auth = vault_auth,
                vault_ata = vault_ata,
            )
        else:
            sell_ix = self._build_old_pumpswap_sell(
                user_pubkey = user_pubkey,
                pool_data = pool_data,
                base_amount_in = base_amount_in,
                min_quote_amount_out = min_quote_amount_out,
                protocol_fee_recipient   = PROTOCOL_FEE_RECIP,
                protocol_fee_recipient_ata = PROTOCOL_FEE_RECIP_ATA,
            )
        instructions.append(sell_ix)
        
        wsol_ata = get_associated_token_address(user_pubkey, pool_data['token_quote'])
        close_ix = close_account(
            CloseAccountParams(
                program_id = TOKEN_PROGRAM_PUB,
                account = wsol_ata,
                dest = user_pubkey,
                owner = user_pubkey
            )
        )
        instructions.append(close_ix)
        
        latest_blockhash = await self.async_client.get_latest_blockhash()
        compiled_msg = MessageV0.try_compile(
            payer=user_pubkey,
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=latest_blockhash.value.blockhash
        )
        transaction = VersionedTransaction(compiled_msg, [self.signer])
        
        opts = TxOpts(skip_preflight=True, max_retries=0)
        send_resp = await self.async_client.send_transaction(transaction, opts=opts)
        if debug_prints:
            print(f"Transaction sent: https://solscan.io/tx/{send_resp.value}")
        
        confirmed = await self.transaction_provider.confirm_transaction(Signature.from_string(str(send_resp.value)))
        if debug_prints:
            print("Success:", confirmed)
        return (confirmed, send_resp.value, pool_type, min_sol_out)

    def _build_new_pumpswap_sell(
        self,
        user_pubkey: Pubkey,
        pool_data: dict,
        base_amount_in: int,
        min_quote_amount_out: int,
        protocol_fee_recipient: Pubkey,
        protocol_fee_recipient_ata: Pubkey,
        vault_auth: Pubkey,
        vault_ata: Pubkey
    ):
        """
        Updated sell instruction for new pump_amm IDL. 
        
        Accounts (19 total based on new IDL):
          #1  Pool
          #2  User
          #3  Global Config
          #4  Base Mint
          #5  Quote Mint
          #6  User Base Token Account
          #7  User Quote Token Account (WSOL ATA)
          #8  Pool Base Token Account
          #9  Pool Quote Token Account
          #10 Protocol Fee Recipient
          #11 Protocol Fee Recipient Token Account
          #12 Base Token Program
          #13 Quote Token Program
          #14 System Program
          #15 Associated Token Program
          #16 Event Authority
          #17 Program
          #18 Coin Creator Vault ATA
          #19 Coin Creator Vault Authority

        Args:
          base_amount_in: u64
          min_quote_amount_out: u64
        """
        data = bytearray()
        data.extend(SELL_INSTR_DISCRIM)
        data.extend(struct.pack("<Q", base_amount_in))
        data.extend(struct.pack("<Q", min_quote_amount_out))

        # Derive event authority for pump_amm
        event_authority = derive_event_authority()

        accs = [
            AccountMeta(pubkey=pool_data["pool_pubkey"], is_signer=False, is_writable=False), # pool
            AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True), # user
            AccountMeta(pubkey=GLOBAL_CONFIG_PUB, is_signer=False, is_writable=False), # global_config
            AccountMeta(pubkey=pool_data["token_base"], is_signer=False, is_writable=False), # base_mint
            AccountMeta(pubkey=pool_data["token_quote"], is_signer=False, is_writable=False), # quote_mint
            AccountMeta(pubkey=get_associated_token_address(user_pubkey, pool_data["token_base"]), is_signer=False, is_writable=True), # user_base_token_account
            AccountMeta(pubkey=get_associated_token_address(user_pubkey, pool_data["token_quote"]), is_signer=False, is_writable=True), # user_quote_token_account
            AccountMeta(pubkey=Pubkey.from_string(pool_data["pool_base_token_account"]), is_signer=False, is_writable=True), # pool_base_token_account
            AccountMeta(pubkey=Pubkey.from_string(pool_data["pool_quote_token_account"]), is_signer=False, is_writable=True), # pool_quote_token_account
            AccountMeta(pubkey=protocol_fee_recipient, is_signer=False, is_writable=False), # protocol_fee_recipient
            AccountMeta(pubkey=protocol_fee_recipient_ata, is_signer=False, is_writable=True), # protocol_fee_recipient_token_account
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False), # base_token_program
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False), # quote_token_program
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False), # system_program
            AccountMeta(pubkey=ASSOCIATED_TOKEN, is_signer=False, is_writable=False), # associated_token_program
            AccountMeta(pubkey=event_authority, is_signer=False, is_writable=False), # event_authority
            AccountMeta(pubkey=PUMPSWAP_PROGRAM_ID, is_signer=False, is_writable=False), # program
            AccountMeta(pubkey=vault_ata, is_signer=False, is_writable=True), # coin_creator_vault_ata
            AccountMeta(pubkey=vault_auth, is_signer=False, is_writable=False), # coin_creator_vault_authority
            # Note: sell instruction doesn't have volume accumulator accounts per IDL
        ]

        return Instruction(
            program_id=PUMPSWAP_PROGRAM_ID,
            data=bytes(data),
            accounts=accs
        )

    def _build_old_pumpswap_sell(
        self,
        user_pubkey: Pubkey,
        pool_data: dict,
        base_amount_in: int,
        min_quote_amount_out: int,
        protocol_fee_recipient: Pubkey,
        protocol_fee_recipient_ata: Pubkey
    ):
        """
        Accounts (17 total):
          #1  Pool
          #2  User
          #3  Global Config
          #4  Base Mint
          #5  Quote Mint
          #6  User Base Token Account
          #7  User Quote Token Account (WSOL ATA)
          #8  Pool Base Token Account
          #9  Pool Quote Token Account
          #10 Protocol Fee Recipient
          #11 Protocol Fee Recipient Token Account
          #12 Base Token Program
          #13 Quote Token Program
          #14 System Program
          #15 Associated Token Program
          #16 Event Authority
          #17 Program

        Data:
          sell_discriminator (8 bytes) + struct.pack("<QQ", base_amount_in, min_quote_amount_out)
        """
        data = bytearray()
        data.extend(SELL_INSTR_DISCRIM)
        data.extend(struct.pack("<Q", base_amount_in))
        data.extend(struct.pack("<Q", min_quote_amount_out))

        accs = [
            AccountMeta(pubkey=pool_data["pool_pubkey"], is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True),
            AccountMeta(pubkey=GLOBAL_CONFIG_PUB, is_signer=False, is_writable=False),
            AccountMeta(pubkey=pool_data["token_base"], is_signer=False, is_writable=False),
            AccountMeta(pubkey=pool_data["token_quote"], is_signer=False, is_writable=False),
            AccountMeta(pubkey=get_associated_token_address(user_pubkey, pool_data["token_base"]), is_signer=False, is_writable=True),
            AccountMeta(pubkey=get_associated_token_address(user_pubkey, pool_data["token_quote"]), is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(pool_data["pool_base_token_account"]), is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(pool_data["pool_quote_token_account"]), is_signer=False, is_writable=True),
            AccountMeta(pubkey=protocol_fee_recipient, is_signer=False, is_writable=False),
            AccountMeta(pubkey=protocol_fee_recipient_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM_PUB, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=ASSOCIATED_TOKEN, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMPSWAP_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

        return Instruction(
            program_id=PUMPSWAP_PROGRAM_ID,
            data=bytes(data),
            accounts=accs
        )


def derive_global_volume_accumulator() -> Pubkey:
    """Derive the global volume accumulator PDA for pump_amm"""
    seed = [b"global_volume_accumulator"]
    return Pubkey.find_program_address(seed, PUMPSWAP_PROGRAM_ID)[0]


def derive_user_volume_accumulator(user: Pubkey) -> Pubkey:
    """Derive the user volume accumulator PDA for pump_amm"""
    seed = [b"user_volume_accumulator", bytes(user)]
    return Pubkey.find_program_address(seed, PUMPSWAP_PROGRAM_ID)[0]


def derive_coin_creator_vault_authority(coin_creator: Pubkey) -> Pubkey:
    """Derive the coin creator vault authority PDA for pump_amm"""
    seed = [b"creator_vault", bytes(coin_creator)]
    return Pubkey.find_program_address(seed, PUMPSWAP_PROGRAM_ID)[0]


def derive_event_authority() -> Pubkey:
    """Derive the event authority PDA for pump_amm"""
    seed = [b"__event_authority"]
    return Pubkey.find_program_address(seed, PUMPSWAP_PROGRAM_ID)[0]


    