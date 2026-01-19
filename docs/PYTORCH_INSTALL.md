# PyTorch Installation for Jetson Nano

JetPack yêu cầu cài PyTorch từ wheel chính thức của NVIDIA.

## JetPack 5.x (L4T R35)

```bash
# Python 3.8/3.10

# PyTorch 2.0 for JetPack 5
wget https://developer.download.nvidia.com/compute/redist/jp/v51/pytorch/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl
pip3 install torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl

# TorchVision
sudo apt install -y libjpeg-dev zlib1g-dev libpython3-dev libopenblas-dev
git clone --branch v0.15.1 https://github.com/pytorch/vision torchvision
cd torchvision
export BUILD_VERSION=0.15.1
python3 setup.py install --user
cd ..
```

## JetPack 4.6 (L4T R32.7)

```bash
# Python 3.6

# PyTorch 1.10 for JetPack 4.6
wget https://nvidia.box.com/shared/static/fjtbno0vpo676a25cgvuqc1wty0fkkg6.whl -O torch-1.10.0-cp36-cp36m-linux_aarch64.whl
pip3 install torch-1.10.0-cp36-cp36m-linux_aarch64.whl

# TorchVision
sudo apt install -y libjpeg-dev zlib1g-dev libpython3-dev libopenblas-dev
git clone --branch v0.11.1 https://github.com/pytorch/vision torchvision
cd torchvision
export BUILD_VERSION=0.11.1
python3 setup.py install --user
cd ..
```

## Verify Installation

```bash
python3 -c "import torch; print(f'PyTorch: {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}')"
```

## TensorRT

TensorRT được cài sẵn với JetPack:

```bash
# Kiểm tra TensorRT
dpkg -l | grep tensorrt

# Python bindings
pip3 install tensorrt

# Verify
python3 -c "import tensorrt; print(tensorrt.__version__)"
```

## ONNX Runtime GPU

```bash
# Cho Jetson (aarch64)
pip3 install onnxruntime-gpu

# Hoặc build từ source nếu cần
# https://onnxruntime.ai/docs/build/eps.html#nvidia-jetson
```

## Links

- [PyTorch for Jetson](https://forums.developer.nvidia.com/t/pytorch-for-jetson/72048)
- [JetPack Archive](https://developer.nvidia.com/embedded/jetpack-archive)
- [TensorRT Python](https://docs.nvidia.com/deeplearning/tensorrt/install-guide/index.html)
