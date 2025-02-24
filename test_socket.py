import asyncio
import websockets

async def echo(websocket):
    print("Client connected.")
    try:
        async for message in websocket:
            print(f"Received from client: {message}")
            response = f"Echo: {message}"
            await websocket.send(response)
            print(f"Sent to client: {response}")
    except websockets.exceptions.ConnectionClosed as e:
        print("Client disconnected.", e)

async def main():
    async with websockets.serve(echo, "localhost", 8765, origins=["*"]):
        print("Server started on ws://localhost:8765")
        await asyncio.Future()  # run forever

if __name__ == '__main__':
    asyncio.run(main())
