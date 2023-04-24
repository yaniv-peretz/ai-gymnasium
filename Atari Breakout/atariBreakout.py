import tensorflow as tf
import collections
import numpy as np
import gymnasium as gym
import matplotlib.pyplot as plt
import tqdm
import atexit
import os
import statistics
from skimage.transform import resize
from typing import Any

# Hyperparameters
filters = 32
actions = 4
kernel_size = (4, 4)
strides = (2, 2)
frames_to_skip = 4
frames_memory_length = 2
max_episodes = 5000
episodes_learning_batch = 64
epsilon = 1
epsilon_decay = 1 / (max_episodes * 0.8)
epsilon_terminal_value = 0.01
learning_rate = 1e-3
gamma = 0.99  # Discount factor for past rewards

# Env Params
env_name = "ALE/Breakout-v5"
render_mode = "rgb_array"
repeat_action_probability = 0
obs_type = "grayscale"
model_file_name = os.path.dirname(__file__) + "/model"
train_model = True

# Plot Params
running_reward_interval = 16

# Demo
max_episodes = 30
epsilon = 0
# render_mode = "human"
train_model = False
load_weights = False
save_weights = False


class Model(tf.keras.Sequential):
    def __init__(self):
        super().__init__()

        # Make Sequenctial-visual model accepts n frames sequence
        self.model = tf.keras.Sequential(
            [
                tf.keras.layers.ConvLSTM2D(
                    filters,
                    kernel_size=8,
                    input_shape=(2, 80, 80, 1),
                    padding="same",
                    strides=4,
                ),
                tf.keras.layers.Conv2D(
                    filters,
                    kernel_size=4,
                    strides=2,
                    activation=tf.keras.activations.relu,
                ),
                tf.keras.layers.Conv2D(
                    filters,
                    kernel_size=3,
                    strides=1,
                    activation=tf.keras.activations.relu,
                ),
                tf.keras.layers.Flatten(),
                tf.keras.layers.Dense(filters, activation=tf.keras.activations.relu),
                tf.keras.layers.Dense(
                    actions,
                    activation=tf.keras.activations.linear,
                    name="output-layer",
                ),
            ]
        )
        self.model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
            loss=tf.keras.losses.Huber(),
        )
        # Training model used for training, and not for taking actions. This would allow a batch update of the model policy.
        self.clone = tf.keras.models.clone_model(self.model)

        self.model.summary()

    def __call__(self, inputs, training=None, mask=None):
        return self.model(inputs)

    @tf.function
    def train(self, reward, time_series: tf.Tensor):
        # Calculate expected Q value based on the stable model
        stable_action_probs = self.clone(time_series)
        stable_q_action = tf.reduce_max(stable_action_probs, axis=1)
        expected_stable_reward = reward + gamma * stable_q_action

        # Calculate loss gradients
        with tf.GradientTape() as tape:
            # Train the model with the state
            action_probs = self.model(time_series)
            q_action = tf.reduce_max(action_probs, axis=1)
            loss = self.model.loss(expected_stable_reward, q_action)  # type: ignore

        # Backpropagation
        grads = tape.gradient(loss, self.model.trainable_variables)
        self.model.optimizer.apply_gradients(zip(grads, self.model.trainable_variables))

    def update_weights(self):
        self.model.set_weights(self.clone.get_weights())


class FramesState(collections.deque):
    def __init__(self, maxlen=frames_memory_length):
        super().__init__(maxlen=maxlen)

    def addFrame(self, observation):
        """Reduce the image center to 80x80 frame"""
        cropped_observation = observation[33:-17]
        img_resized = resize(
            cropped_observation, output_shape=(80, 80), anti_aliasing=True
        )
        self.append(img_resized)

    def reset(self):
        self.clear()
        self.append(np.zeros((80, 80)))
        self.append(np.zeros((80, 80)))

    def getTensor(self) -> tf.Tensor:
        return tf.expand_dims([self[0], self[1]], axis=0)


# Main
print("Num GPUs Available: ", len(tf.config.list_physical_devices("GPU")))

model = Model()

# load and save weights
if load_weights:
    try:
        model.load_weights(model_file_name)
    except:
        print("Model wasn't found in: {model_file_name}")
if save_weights:
    atexit.register(lambda: model.save_weights(model_file_name))

frames_state = FramesState(maxlen=frames_memory_length)

env = gym.make(
    env_name,
    render_mode=render_mode,
    repeat_action_probability=repeat_action_probability,
    obs_type=obs_type,
)

total_frames = 0
episodes_reward: collections.deque[int] = collections.deque(
    maxlen=running_reward_interval
)
rewards: list[float] = []

# Training Loop (episode, frame)
max_episodes_tqdm = tqdm.trange(max_episodes)
for episode in max_episodes_tqdm:
    env.reset()
    frames_state.reset()
    done = False
    episode_reward = 0
    step = 0

    while done != True:
        total_frames += 1
        step += 1
        if step % frames_to_skip != 0:
            observation, reward, terminated, truncated, info = env.step(1)
            if reward > 0:
                model.train(reward=reward, time_series=frames_state.getTensor())
                episode_reward += reward
            continue

        # epsilon-greedy selection
        if epsilon > 0 and np.random.random() < epsilon:
            action = env.action_space.sample()
        else:
            time_series = frames_state.getTensor()
            action_probs: Any = model(time_series)
            action = tf.argmax(action_probs[0]).numpy()

        observation, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        episode_reward += reward

        if train_model:
            model.train(reward=reward, time_series=frames_state.getTensor())

        # Prepare next step state
        frames_state.addFrame(observation)

        if episode > 1 and episode % episodes_learning_batch == 0:
            model.update_weights()

    # Print post episode
    episodes_reward.append(int(episode_reward))
    if episode % running_reward_interval == 0:
        rewards.append(statistics.mean(episodes_reward))

    # epsilon decay
    if epsilon > 0 and total_frames > 10000:
        epsilon -= epsilon_decay
        if epsilon < epsilon_terminal_value:
            epsilon = 0

    max_episodes_tqdm.set_postfix(
        episode=episode,
        reward=episode_reward,
        epsilon=epsilon,
        total_frames=total_frames,
    )

# Plot Rewards Progression
steps = np.array(range(0, len(rewards), 1))
plt.plot(steps, rewards)
plt.ylabel("Reward")
plt.xlabel("Episode")
plt.ylim()
plt.title("Rewards Progression")
plt.show()
