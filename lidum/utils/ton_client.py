import asyncio
from os import makedirs
from typing import Literal

import requests
from pytonapi import Tonapi
from pytonlib import TonlibClient
from ton.utils import read_address
from tonsdk.boc import Cell, Slice
from tonsdk.utils import Address, b64str_to_bytes
from pytonlib.tonlibjson import TonlibError
from tonsdk.contract.token.nft import NFTItem, NFTCollection

from .wallet import LIDUM_WALLET, LIDUM_WALLET_ADDRESS
from ..config import ROYALTY, LS_CONFIG, TONAPI_KEY
from ..config import MINT_TIMEOUT, ROYALTY_BASE, KEYSTORE_PATH
from ..config import FORWARD_AMOUNT, TONLIB_TIMEOUT
from ..config import TRANSFER_TIMEOUT, LS_CONFIG_TESTNET
from ..config import NFT_TRANSFER_AMOUNT
from ..config import COLLECTION_TRANSFER_AMOUNT
from ..config import NFT_TRANSFER_FORWARD_AMOUNT


class TonClient:
    """Класс для взаимодействия с блокчейном TON через библиотеку pytonlib.

    Этот класс переопределяет часть методов класса TonlibClient для автоматической инициализации и завершения клиента, а также
    для автоматической смены доступных лайт-серверов в случае появления ошибок от конкретного лайт-сервера.

    :param bool is_testnet: Использовать ли конфигурацию для сети TestNet.

    :param int | Literal["auto"] ls_index: Индекс лайтсервера для подключения. Если указано "auto",
        будет автоматически переберать доступные лайт-сервера, начиная с нулевого. По умолчанию, `'auto'`.

    :param int ls_retry_cnt: Максимальное количество обходов всех лайт-серверов при отправке одного сообщения.
        Имеет эффект только при значении `ls_index='auto'`.

    :param int config_retry_cnt: Количество попыток получения конфигурация лайт-серверов.

    :param int run_method_retry_cnt: Количество попыток выполнения метода смарт-контракта.

    :param bool verbose: Выводить ли информацию о совершении действий и их ошибки.

    Attributes:
        is_testnet (bool): Использовать ли конфигурацию для сети TestNet.
        config (dict): Конфигурация лайтсерверов, полученная для выбранной сети.
        ls_cnt (int): Количество доступных лайтсерверов в конфигурации.
        ls_index (int | str): Индекс лайтсервера или автоопределение.
        ls_retry_cnt (int): Максимальное количество обходов всех лайт-серверов при отправке одного сообщения.
        config_retry_cnt (int): Количество попыток получения конфигурация лайт-серверов.
        raw_method_retry_cnt (int): Количество попыток выполнения метода смарт-контракта.
        verbose (bool): Режим вывода информации.
        client (TonlibClient): Экземпляр клиента TonlibClient для работы с блокчейном.

    Examples:
    ```python
    client = TonClient(is_testnet=True, ls_index="auto", ls_retry_cnt=5, verbose=True)

    nft_address = await client.deploy_one_item(
        collection_address="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        nft_meta="/path/to/nft/metadata.json"
    )
    ```
    """

    def __init__(self,
                 is_testnet: bool,
                 ls_index: int | Literal["auto"] = "auto",
                 ls_retry_cnt: int = 3,
                 config_retry_cnt: int = 3,
                 run_method_retry_cnt: int = 10,
                 verbose: bool = False):

        self.is_testnet = is_testnet
        self.verbose = verbose
        self.ls_index = ls_index
        self.ls_retry_cnt = ls_retry_cnt
        self.config_retry_cnt = config_retry_cnt
        self.run_method_retry_cnt = run_method_retry_cnt

        self.config = self.get_config()
        self.ls_cnt = len(self.config["liteservers"])

        makedirs(KEYSTORE_PATH, exist_ok=True)

        self.client = TonlibClient(
            ls_index=ls_index if isinstance(ls_index, int) else 0,
            config=self.config,
            keystore=KEYSTORE_PATH,
            tonlib_timeout=TONLIB_TIMEOUT,
        )

    def get_config(self):
        """Возвращает список лайт-серверов для указанной сети.

        :return: Список лайт-серверов в формате JSON
        :rtype: dict

        :raise HTTPError: После `config_retry_cnt` неудачных попыток получения ответа
        """
        config_url = LS_CONFIG_TESTNET if self.is_testnet else LS_CONFIG

        for i in range(self.config_retry_cnt):

            if self.verbose:
                print(f"Attempt to get the configuration file from {config_url} {i + 1} / {self.config_retry_cnt}...")

            response = requests.get(config_url)

            if response.status_code != 200:

                if self.verbose:
                    print(f"Error when receiving a response from {config_url}")

                continue

            if self.verbose:
                print(f"The response from {config_url} has been received!")

            return response.json()

        if self.verbose:
            print(f"Error when receiving config from {config_url}")

        response.raise_for_status()

    async def raw_send_message(self,
                               to_addr: str,
                               amount: str,
                               payload: Cell | str | bytes | None = None,
                               state_init: Cell | None = None):
        """Отправляет сообщение в блокчейн через TonlibClient. В режиме "auto"
        перебирает доступные лайт-сервера, если при отправке сообщения возникает ошибка
        лайт-сервера. Производит полный перебор доступных лайт-серверов `ls_retry_cnt`
        раз.

        :param str to_addr: Адрес смарт-контракта в raw или user-friendly для отправки
            на него сообщения.
        :param str amount: Количество нанотон для отправки сообщения. :param Cell | str
            | bytes | None payload: Полезная нагрузка, прикрепляемая к сообщению. :param
            Cell | None state_init: Ячейка с инициализирующим сообщением.
        :return: Статус отправки сообщения.
        :rtype: bool
        """
        for i in range(self.ls_retry_cnt):

            if self.verbose:
                print(f"Attempt to send a message {i + 1} / {self.ls_retry_cnt}...")

            for ls_id in range(self.ls_cnt):

                try:
                    query = LIDUM_WALLET.create_transfer_message(to_addr=to_addr,
                                                                 amount=amount,
                                                                 seqno=await self.seqno,
                                                                 payload=payload,
                                                                 state_init=state_init)

                    if self.ls_index == "auto":
                        self.client.ls_index = ls_id

                    if self.verbose:
                        print(f"An attempt to send a message to the ls with the index {self.client.ls_index}")

                    await self.client.init()
                    await self.client.raw_send_message(query["message"].to_boc(False))

                    if self.verbose:
                        print(f"Sending a message to the light server with the index {self.client.ls_index} was successful")

                    return True

                except TonlibError as e:

                    if self.verbose:
                        print(f"An error occurred when sending a message on a ls with the index {self.client.ls_index}: {e}")

                    if self.ls_index != "auto":

                        if self.verbose:
                            print(
                                f"Sending a message to the light server with the index {self.client.ls_index} was unsuccessful")

                    await asyncio.sleep(1)

                finally:
                    await self.client.close()

        if self.verbose:
            print(f"Sending a message to the light server with the index {self.client.ls_index} was unsuccessful")

        return False

    async def raw_get_account_state(self, address: str):
        """Возвращает данные смарт-контракта.

        :param str address: Адрес смарт-контракта в raw или user-friendly.

        :rtype: dict
        :return: Словарь вида
            ```python
            {
                '@type': 'raw.accountState',
                'balance': str,
                'code': str,
                'data': str,
                'last_transaction_id': internal.transactionId,
                'sync_utime': int
            }
            ```
        """
        try:
            await self.client.init()

            if self.verbose:
                print(f"Getting the account state for the {address} address...")

            data = await self.client.raw_get_account_state(address)
            return data

        except Exception as e:
            print(f"Error receiving account state {address}: {e}")

        finally:
            await self.client.close()

    async def raw_run_method(self, address: str, method: str, stack_data: list[list[str | str | dict]]):
        """Запускает метод смарт-контракта.

        :param str address: Адрес смарт-контракта в raw или user-friendly.
        :param str method: Метод смарт-контракта.
        :param list stack_data: Аргументы для выполнения метода смарт-контракта.

        :rtype: dict
        :return: Словарь вида
            ```python
            {
                '@type': 'smc.runResult',
                'gas_used': int,
                'stack': List[List[Union[str, str, dict]]],
                'exit_code': int,
                '@extra': str,
                'block_id': dict,
                'last_transaction_id': dict
            }
            ```

        :raise Exception: При превышении количества попыток выполнения метода смарт-контракта.
        """
        for i in range(self.run_method_retry_cnt):
            try:
                await self.client.init()

                if self.verbose:
                    print(f"Attemp to get stack data for a smart contract {address}"
                          f"via the {method} method with stack_data {stack_data}"
                          f"{i + 1} / {self.run_method_retry_cnt}...")

                stack = await self.client.raw_run_method(address=address, method=method, stack_data=stack_data)

                if "exit_code" not in stack or stack["exit_code"] != 0:

                    if self.verbose:
                        print("Stack data has not been received. Retrying...")

                    await asyncio.sleep(1)
                    continue

                return stack

            except Exception as e:

                raise (f"Error when receiving stack data for a smart contract {address}"
                       f"via the {method} method with stack_data {stack_data}: {e}") from e

            finally:
                await self.client.close()

        raise Exception(f"Exceeded the number of attempts to get stack data for a smart contract {address}"
                        f"via the {method} method with stack_data {stack_data}.")

    async def get_transactions(self, hash: str, limit: int = 10):
        """Возвращает список последних транзакций, связанных с кошельком приложения."""

        try:
            await self.client.init()
            data = await self.client.get_transactions(account=LIDUM_WALLET_ADDRESS,
                                                      from_transaction_lt=0,
                                                      from_transaction_hash=hash,
                                                      limit=limit)

            return data

        except Exception as e:
            print(f"Error in receiving transactions: {e}")

        finally:
            await self.client.close()

    async def collection_last_index(self, collection_address: str):
        """Возвращает индекс последнего элемента в коллекции.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :return: Индекс последнего элемента в коллекции.
        :rtype: int
        """
        try:
            if self.verbose:
                print(f"Getting the last index from the collection {collection_address}...")

            state = await self.raw_run_method(
                address=collection_address,
                method="get_collection_data",
                stack_data=[],
            )

            return int(state["stack"][0][1], 16)

        except Exception as e:
            print(f"Error when getting the last index from the collection {collection_address}: {e}")

    async def nft_address_by_index(self, collection_address: str, index: int):
        """Возвращает адрес NFT по его индексу в коллекции.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :param int index: Индекс NFT в коллекции.
        :return: Адрес NFT в user-friendly.
        :rtype: str
        """
        try:
            if self.verbose:
                print(f"Getting the NFT address from collection {collection_address} by index {index}...")

            stack = await self.raw_run_method(
                address=collection_address,
                method="get_nft_address_by_index",
                stack_data=[["number", index]],
            )

            nft_address = Cell.one_from_boc(b64str_to_bytes(stack["stack"][0][1]["bytes"]))
            nft_address = read_address(nft_address).to_string(True, True, True)

            return nft_address

        except Exception as e:
            print(f"Error when getting the NFT address from collection {collection_address} by index {index}: {e}")

    async def get_nft_owner(self, nft_address: str):
        """Возвращает адрес владельца NFT.

        :param str nft_address: Адрес NFT в raw или user-friendly.
        :return: Адрес владельца NFT в user-friendly.
        :rtype: str
        """
        try:
            if self.verbose:
                print(f"Getting the owner of NFT {nft_address}...")

            stack = await self.raw_run_method(address=nft_address, method="get_nft_data", stack_data=[])

            owner_address = Cell.one_from_boc(b64str_to_bytes(stack["stack"][3][1]["bytes"]))
            owner_address = Slice(owner_address).read_msg_addr()
            owner_address = Address(owner_address).to_string(True, True, True)

            return owner_address

        except Exception as e:
            print(f"Error when getting the owner of NFT {nft_address}: {e}")
            return

    async def raw_estimate_fees(self, destination, body, init_code=b"", init_data=b"", ignore_chksig=True):
        pass

    async def deploy_collection(self, collection: NFTCollection):
        """Минт пустой коллекции.

        :param NFTCollection collection: Инициализированная ячейка с данными о
            коллекции.
        :return: Статус минта.
        :rtype: bool
        """
        state_init = collection.create_state_init()["state_init"]
        collection_address = collection.address.to_string(True, True, True)

        if self.verbose:
            print(f"Starting the deployment of the collection with the address {collection_address}...")

        # Проверка на существование коллекции с таким адресом на кошельке
        if self.verbose:
            print(f"Checking the existence of collection {collection_address} on the wallet...")

        data = await self.raw_get_account_state(collection_address)

        if data["code"] != "":

            if self.verbose:
                print(f"The collection with the address {collection_address} already exists!")

            return True

        if self.verbose:
            print(f"Sending a message to mint the collection {collection_address}...")

        # Отправка сообщения на минт коллекции
        sent = await self.raw_send_message(to_addr=collection_address, amount=COLLECTION_TRANSFER_AMOUNT, state_init=state_init)

        if not sent:

            if self.verbose:
                print(f"Sending a message to mint collection {collection_address} was unsuccessful!")

            return False

        # Ожидание появления пустой коллекции на кошельке
        timeout_cnt = 0

        if self.verbose:
            print(f"Waiting for the end of the collection's minting with the address {collection_address}...")

        while True:

            if timeout_cnt > MINT_TIMEOUT:

                if self.verbose:
                    print(f"The waiting time for the end of the collection's minting has been exceeded {collection_address}!")

                return False

            data = await self.raw_get_account_state(collection_address)

            if data is not None and data["code"] != "":

                if self.verbose:
                    print(f"Collection {collection_address} has been successfully minted!")

                return True

            await asyncio.sleep(1)
            timeout_cnt += 1

    async def deploy_one_item(self, collection_address: str, nft_meta: str):
        """Минт одного NFT в существующую коллекцию.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :param str nft_meta: URL этого NFT.
        :return: Адрес сминченного NFT в user-friendly.
        :rtype: str
        """
        if self.verbose:
            print(f"Starting the deployment of the NFT with metadata {nft_meta}"
                  f"to the collection with the address {collection_address}...")

        body = await self.nft_mint_body(
            collection_address=collection_address,
            nft_meta=nft_meta,
        )

        if self.verbose:
            print("Defining a new NFT index and address...")

        last_index = await self.collection_last_index(collection_address)
        new_nft_address = await self.nft_address_by_index(collection_address, last_index)

        if self.verbose:
            print(f"The new NFT will have an index of {last_index} and an address of {new_nft_address}")

        sent = await self.raw_send_message(
            to_addr=collection_address,
            amount=NFT_TRANSFER_AMOUNT,
            payload=body,
        )

        if not sent:

            if self.verbose:
                print(f"Sending a message to mint an NFT with the address {new_nft_address}"
                      f"to the collection {collection_address} was unsuccessful!")

            return None

        # Ожидание появления NFT в коллекции
        timeout_cnt = 0

        if self.verbose:
            print(f"Waiting for the end of the NFT minting with the address {new_nft_address}...")

        while timeout_cnt <= MINT_TIMEOUT:

            data = await self.raw_get_account_state(new_nft_address)

            if data is not None and data["code"] != "":

                if self.verbose:
                    print(f"The NFT with the address {new_nft_address} has been successfully minted.")

                return new_nft_address

            await asyncio.sleep(1)
            timeout_cnt += 1

        if self.verbose:
            print(f"The waiting time for NFT minting with address {new_nft_address} has been exceeded!")

        return None

    async def deploy_batch_items(self, collection_address: str, nfts_num: int, nft_meta: str):
        """Минт батча NFT в существующую коллекцию.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :param int nfts_num: Количество NFT, которое нужно заминтить.
        :param str nft_meta: URL этого NFT.
        :return: Адрес сминченных NFT в user-friendly.
        :rtype: List[str]
        """
        if self.verbose:
            print(f"Starting the deployment of the NFTs with metadata {nft_meta}"
                  f"to the collection with the address {collection_address}...")

        body = await self.batch_mint_body(
            collection_address=collection_address,
            nfts_num=nfts_num,
            nft_meta=nft_meta,
        )

        if self.verbose:
            print("Defining a new NFT indexes and addresses...")

        last_index = await self.collection_last_index(collection_address)
        new_nft_addresses = []

        for i in range(nfts_num):
            new_address = await self.nft_address_by_index(collection_address, last_index + i)
            new_nft_addresses.append(new_address)

        if self.verbose:
            print(f"The new NFTs will have indexes {last_index} and addresses {new_nft_addresses}")

        sent = await self.raw_send_message(to_addr=collection_address,
                                           amount=nfts_num * FORWARD_AMOUNT + NFT_TRANSFER_AMOUNT,
                                           payload=body)

        if not sent:

            if self.verbose:
                print(f"Sending a message to mint the NFTs with addresses {new_nft_addresses}"
                      f"to the collection {collection_address} was unsuccessful!")

            return None

        # Ожидание появления NFT в коллекции
        timeout_cnt = 0

        if self.verbose:
            print(f"Waiting for the end of the NFTs minting with addresses {new_nft_addresses}...")

        while timeout_cnt <= MINT_TIMEOUT:

            for address in new_nft_addresses:
                data = await self.raw_get_account_state(address)

                if data is not None and data["code"] == "":
                    await asyncio.sleep(1)
                    timeout_cnt += 1
                    continue

                if self.verbose:
                    print(f"The NFTs with addresses {new_nft_addresses} has been successfully minted.")

                return new_nft_addresses

        if self.verbose:
            print(f"The waiting time for the NFTs minting with addresses {new_nft_addresses} has been exceeded!")

        return None

    async def transfer_nft(self, nft_address: str, new_owner_address: str):
        """Переводит NFT с кошелька приложения на адрес пользователя.

        :param str nft_address: Адрес NFT в raw или user-friendly.
        :param str new_owner_address: Адрес кошелька пользователя в raw или user-
            friendly.
        :return: Статус выполнения перевода.
        :rtype: bool
        """
        if self.verbose:
            print(f"Starting the transfer of the NFT with the address {nft_address} to the address {new_owner_address}...")

        if self.verbose:
            print(f"Verification of the current owner of the NFT with the address {nft_address}...")

        # Начальная проверка владельца NFT
        nft_owner = await self.get_nft_owner(nft_address=nft_address)

        if nft_owner == new_owner_address:

            if self.verbose:
                print(f"The NFT with the {nft_address} address already belongs to the {new_owner_address} wallet!")

            return True

        elif nft_owner != LIDUM_WALLET_ADDRESS:

            if self.verbose:
                print(f"The NFT with address {nft_address} belongs to another address {new_owner_address}!")

            return True

        body = NFTItem().create_transfer_body(
            new_owner_address=Address(new_owner_address),
            response_address=Address(LIDUM_WALLET_ADDRESS),
            forward_amount=NFT_TRANSFER_FORWARD_AMOUNT,
        )

        if self.verbose:
            print(f"Transfer an NFT transmission message with address {nft_address} to address {new_owner_address}...")

        sent = await self.raw_send_message(
            to_addr=nft_address,
            amount=NFT_TRANSFER_AMOUNT,
            payload=body,
        )

        if not sent:

            if self.verbose:
                print(f"Sending a message to transfer an NFT with the address {nft_address}"
                      f"to address {new_owner_address} was unsuccessful!")

            return False

        # Ожидание перевода NFT
        timeout_cnt = 0

        if self.verbose:
            print(f"Waiting for the end of the NFT transfer with the address {nft_address}...")

        while timeout_cnt <= TRANSFER_TIMEOUT:

            nft_owner = await self.get_nft_owner(nft_address=nft_address)

            if nft_owner == new_owner_address:

                if self.verbose:
                    print(f"The NFT with address {nft_address} has been successfully sent to address {new_owner_address}!")

                return True

            await asyncio.sleep(1)
            timeout_cnt += 1

        if self.verbose:
            print(f"The waiting time for the transfer of NFT with address {nft_address}"
                  f"to address {new_owner_address} has been exceeded!")

        return False

    def collection_mint_body(self, collection_content_uri: str, nft_item_content_base_uri: str):
        """Возвращает инициализированную ячейку с данными о коллекции.

        :param str collection_content_uri: URL метаданных коллекции в формате JSON.
        :param str nft_item_content_base_uri: URL директории, в которой хранятся
            метаданные NFT этой коллекции.
        :return: Инициализированная ячейка с данными коллекции.
        :rtype: NFTCollection
        """

        collection = NFTCollection(
            royalty_base=ROYALTY_BASE,
            royalty=ROYALTY,
            royalty_address=Address(LIDUM_WALLET_ADDRESS),
            owner_address=Address(LIDUM_WALLET_ADDRESS),
            collection_content_uri=collection_content_uri,
            nft_item_content_base_uri=nft_item_content_base_uri,
            nft_item_code_hex=NFTItem.code,
        )

        return collection

    async def nft_mint_body(self, collection_address: str, nft_meta: str):
        """Возвращает инициализированную ячейку с данными о NFT.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :param str nft_meta: URL метаданных этого NFT в формате JSON.
        :return: Инициализированная ячейка с данными NFT.
        :rtype: Cell
        """

        body = NFTCollection().create_mint_body(
            item_index=await self.collection_last_index(collection_address),
            new_owner_address=Address(LIDUM_WALLET_ADDRESS),
            item_content_uri=nft_meta,
            amount=FORWARD_AMOUNT,
        )

        return body

    async def batch_mint_body(self, collection_address: str, nfts_num: int, nft_meta: str):
        """Возвращает инициализированную ячейку с данными о нескольких NFT.

        :param str collection_address: Адрес коллекции в raw или user-friendly.
        :param int nfts_num: Количество NFT, которое нужно сминтить.
        :param str nft_meta: URL метаданных этих NFT в формате JSON. Для всех NFT
            используется один файл метаданных.
        :return: Инициализированная ячейка с данными NFT.
        :rtype: Cell
        """

        contents_and_owners = [(nft_meta, Address(LIDUM_WALLET_ADDRESS)) for _ in range(nfts_num)]

        body = NFTCollection().create_batch_mint_body(
            from_item_index=await self.collection_last_index(collection_address),
            contents_and_owners=contents_and_owners,
            amount_per_one=FORWARD_AMOUNT,
        )

        return body

    @property
    async def seqno(self):
        """Возвращает текущий seqno кошелька приложения.

        :return: Текущий seqno.
        :rtype: int
        """

        try:
            await self.client.init()

            data = await self.client.raw_run_method(method="seqno", stack_data=[], address=LIDUM_WALLET_ADDRESS)

            await self.client.close()
            return int(data["stack"][0][1], 16)

        except Exception as e:
            print(f"Error when getting seqno: {e}")

        finally:
            await self.client.close()


def get_transaction_data(hash: str, is_testnet: bool):

    tonapi = Tonapi(api_key=TONAPI_KEY, is_testnet=is_testnet)
    return tonapi.blockchain.get_transaction_data(hash)
